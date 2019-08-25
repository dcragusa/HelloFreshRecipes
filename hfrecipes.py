import os
import json
import math
from multiprocessing import Pool

import requests
from fpdf import FPDF

USERNAME = '***'
PASSWORD = '***'

LOGIN_LINK = 'https://www.hellofresh.com/gw/login'
API_LINK = 'https://gw.hellofresh.com/api/recipes/search'
IMAGE_URL = 'https://res.cloudinary.com/hellofresh/image/upload/f_{},q_auto,h_{}/v1/hellofresh_s3'
COMMON_INGREDIENTS = ['Salt', 'Pepper', 'Sugar', 'Butter']  # don't include these in the ingredient list


class AttrDict(dict):
    # access data by dot notation e.g. {'a': 1} -> d.a = 1
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.__dict__ = self

    def __getattr__(self, name):
        return self.get(name, None)


def get_or_make_dir(base_path, dir_name):
    directory = os.path.join(base_path, dir_name)
    os.makedirs(directory, exist_ok=True)
    return directory


class HelloFreshRecipes:

    s = requests.Session()
    s.params.update({
        'locale': 'en-US',
        'country': 'us'
    })
    # pretend to be the latest Firefox
    s.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:68.0) Gecko/20100101 Firefox/68.0'
    })

    def __init__(self):
        self.RECIPE_DIR = get_or_make_dir(os.path.abspath('.'), 'recipes')
        self.TEMP_DIR = get_or_make_dir(os.path.abspath('.'), 'tmp')
        self.ACCESS_TOKEN = None  # obtain later

    def login(self):
        print('Logging in...')
        login_payload = {
            'username': USERNAME,
            'password': PASSWORD
        }
        resp = self.s.post(LOGIN_LINK, data=login_payload)
        if resp.status_code != 200:
            raise Exception(resp)
        return resp.json()['access_token']

    def collect_recipes(self):

        self.ACCESS_TOKEN = self.login()

        print('Collecting recipes...')
        all_recipes = []
        i = 0

        while True:
            data_payload = {'offset': i * 250, 'limit': (i + 1) * 250}  # 250 max items per call
            data_header = {'Authorization': f'Bearer {self.ACCESS_TOKEN}'}

            resp = self.s.get(API_LINK, headers=data_header, params=data_payload)
            if resp.status_code != 200:
                raise Exception(resp)
            recipes = resp.json()['items']
            all_recipes.extend(recipes)

            if len(recipes) < 250:
                # we have reached end of recipes
                break

            i += 1

        with open('recipes.json', 'w') as f:
            json.dump(all_recipes, f)

    def save_image(self, url, name, ext, height):

        name = f'{name}.{ext}'
        path = os.path.join(self.TEMP_DIR, name)
        if os.path.isfile(path):
            # file already exists
            return path

        resp = self.s.get(IMAGE_URL.format(ext, height) + url, stream=True)
        if resp.status_code != 200:
            raise Exception(resp)
        with open(path, 'wb') as f:
            for chunk in resp:
                f.write(chunk)

        return path

    @staticmethod
    def get_ingredient_details(recipe, idx):
        amount_details = recipe.yields[0]['ingredients'][idx]
        amount = amount_details['amount']
        pluralise = 's' if amount is not None and amount > 1 else ''
        amount = str(amount).replace('0.25', '¼').replace('0.5', '½').replace('0.75', '¾')
        units = amount_details['unit']
        name = recipe.ingredients[idx]['name']
        if units is None:
            return name, ''
        if units == 'unit':
            return amount, name
        else:
            return f'{amount} {units}{pluralise}', name

    @staticmethod
    def write(pdf, size, x, y, text):
        pdf.set_font_size(size)
        pdf.set_xy(x, y)
        pdf.write(0, text)
        return pdf

    @staticmethod
    def multi_cell(pdf, size, x, y, width, height, text):
        pdf.set_font_size(size)
        pdf.set_xy(x, y)
        pdf.multi_cell(width, height, text)
        return pdf

    @staticmethod
    def prepare_str(string, filename=False):
        if string is None:
            return ''
        string = string.replace('\n', ' ').replace(u'\u2060', '')
        if filename:
            # not allowed in Windows filenames
            string = string.replace('\"', '\'').replace(u'*', '')
        return string

    def process_all_recipes(self):
        with open('recipes.json', 'r') as f:
            recipes = json.load(f)
        with Pool(os.cpu_count()) as pool:
            pool.map(self.process_recipe, recipes)

    def process_recipe(self, recipe):

        recipe = AttrDict(recipe)

        # malformed recipes
        if not recipe.steps or not recipe.ingredients:
            return

        if ' with' in recipe.name:
            recipe.name, remainder = recipe.name.split(' with')
            recipe.headline = 'With' + remainder

        dest_dir = self.RECIPE_DIR if recipe.author is None else get_or_make_dir(self.RECIPE_DIR, recipe.author)
        path = os.path.join(dest_dir, self.prepare_str(f'{recipe.name}.pdf', True))
        if os.path.exists(path):
            return

        print(f'Processing {recipe.name}...')

        pdf = FPDF(orientation='P', unit='mm', format='A4')
        pdf.add_font('segoe', '', r'C:\Windows\Fonts\segoeui.ttf', uni=True)  # guaranteed in Win10
        pdf.set_font('segoe')
        pdf.set_auto_page_break(False)
        pdf.add_page()

        # Title, description, other attributes
        pdf = self.write(pdf, 14, 10, 15, self.prepare_str(recipe.name))
        pdf = self.write(pdf, 10, 10, 20, self.prepare_str(recipe.headline))
        pdf = self.write(pdf, 10, 10, 25, f'For 2 people, {recipe.nutrition[1]["amount"]} cal per serving')
        pdf = self.multi_cell(pdf, 7, 10, 30, 110, 4, self.prepare_str(recipe['description']))

        # Main illustrative image
        img = self.save_image(recipe.imagePath, self.prepare_str(f'{recipe.slug}-main', True), 'jpg', 600)
        pdf.image(img, x=125, y=10, w=72)

        # Ingredients
        num_ingr = 0
        for idx, ingr in enumerate(recipe.ingredients):
            if ingr['name'] in COMMON_INGREDIENTS:
                continue

            if ingr['imagePath']:
                img = self.save_image(ingr['imagePath'], self.prepare_str(ingr['name'], True), 'png', 200)
                pdf.image(img, x=10 + 46*(num_ingr % 4), y=62 + 13*math.floor(num_ingr/4), h=12)

            ln_1, ln_2 = self.get_ingredient_details(recipe, idx)
            pdf = self.write(pdf, 9, 22 + 46*(num_ingr % 4), 66 + 13*math.floor(num_ingr/4), ln_1)
            pdf = self.write(pdf, 7, 22 + 46*(num_ingr % 4), 70 + 13*math.floor(num_ingr/4), ln_2)
            num_ingr += 1

        # Recipe steps
        steps_y = 67 + 13*math.ceil(num_ingr/4)

        for idx, step in enumerate(recipe.steps):
            if step['images'] and step['images'][0]['path']:
                img = self.save_image(
                    step['images'][0]['path'], self.prepare_str(f'{recipe.slug}-step_{idx}', True), 'jpg', 400
                )
                pdf.image(img, x=10, y=steps_y + (idx*26), w=36)
            pdf = self.multi_cell(pdf, 10, 50, steps_y + (idx*26), 145, 4, self.prepare_str(step['instructions']))

        pdf.output(path)
        print(f'Output {recipe.name}')


if __name__ == '__main__':
    api = HelloFreshRecipes()
    api.save_items()
    api.process_all_recipes()
