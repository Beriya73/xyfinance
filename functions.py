from tabulate import tabulate
from termcolor import cprint,colored
from config import CHAINS
# Выбираем сеть
def get_chain(chains:dict, issue:str):
    # Преобразуем ключи словаря в список для выбора
    keys = list(chains.keys())
    table = [[i + 1, keys[i]] for i in range(len(keys))]

    # Выводим в консоль таблицу с номерами и ключами
    cprint(tabulate(table, headers=["Номер", "Ключ"], tablefmt="grid"), 'light_green')

    # Запрашиваем у пользователя выбор ключа по номеру
    while True:
        try:
            choice = int(input(colored(f'{issue}','light_green')))
            if 1 <= choice <= len(keys):
                selected_key = keys[choice - 1]
                cprint(f"Вы выбрали сеть: {selected_key}", 'light_green')
                return selected_key
            else:
                cprint(f"Пожалуйста, введите число от 1 до {len(keys)}.", 'light_green')
        except ValueError:
            cprint("Пожалуйста, введите корректное число.", 'light_yellow')

# Получаем количество токена для вывода в wei
def get_amount()->int:
    while True:
        try:
            amount = float(input(colored("Введите сумму перевода нативного токена: ", 'light_green')))
            return amount
        except ValueError:
            cprint("Пожалуйста, введите корректное число.", 'light_yellow')

        # Вычисляем путь с максимальным minReceiveAmount
def calculate_best_path(allowance: dict) -> dict:
    cprint("Вычисляем путь с лучшей ценой", 'light_green')
    max_value = 0
    max_route = None
    key = 'minReceiveAmount'
    for route in allowance['routes']:
        if key in route and float(route[key]) > max_value:
            max_value = float(route[key])
            max_route = route
    return max_route