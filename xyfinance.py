import asyncio
from web3 import AsyncWeb3, AsyncHTTPProvider
from web3.exceptions import TransactionNotFound
import os
from dotenv import load_dotenv
from aiohttp import ClientSession, TCPConnector
from aiohttp_socks import ProxyConnector
from config import ETH_MASK, CHAINS
from termcolor import cprint, colored
from functions import get_chain, calculate_best_path

# Получение данных из файла .env
load_dotenv()
proxy = os.getenv('proxy')
private_key = os.getenv("private_key")


class XYFin:
    def __init__(self, proxy):
        self.eip_1559 = True
        # Настройка параметров запроса с использованием прокси, если он указан
        if proxy is None:
            cprint("Предупреждение: Прокси не указан. Запросы будут отправляться напрямую.", 'light_yellow')
        request_kwargs = {'proxy': f'http://{proxy}'} if proxy else {}
        self.proxy = proxy
        self.chains = CHAINS
        self.from_chain = get_chain(CHAINS, 'Выберите сеть откуда будем выводить нативный токен: ')
        self.to_chain = get_chain(CHAINS, 'Выберите сеть куда будем выводить нативный токен: ')
        self.explorer_url = CHAINS[self.from_chain]['explorer']
        self.w3 = AsyncWeb3(
            AsyncHTTPProvider(CHAINS[self.from_chain]['rpc'], request_kwargs=request_kwargs))  # Инициализация Web3
        self.private_key = private_key  # Сохранение приватного ключа
        try:
            self.address = self.w3.to_checksum_address(
                self.w3.eth.account.from_key(self.private_key).address)  # Получение адреса кошелька
        except ValueError:
            cprint('Указанный private_key некорректен', 'light_red')
            exit(1)

    @staticmethod
    async def make_request(
            method: str = 'GET', url: str = None, params: dict = None, headers: dict = None, json: dict = None):
        """
        Функция для выполнения асинхронного HTTP-запроса с использованием прокси, если он указан.
        """
        async with ClientSession(
                connector=ProxyConnector.from_url(f"http://{proxy}") if proxy else TCPConnector()) as session:
            async with session.request(method=method, url=url, params=params, headers=headers, json=json) as response:
                if response.status == 200:
                    response_json = await response.json()
                    return response_json
                elif response.status == 404:
                    cprint(f"Ошибка : Not Found", 'light_red')
                    exit(1)
                elif response.status == 422:
                    cprint(f"Ошибка : Not Validation Error", 'light_red')
                    exit(1)
                raise RuntimeError(f"Bad request to XYFinance API. Response status: {response.status}")

    async def get_balance(self):
        # Получение баланса кошелька.
        return await self.w3.eth.get_balance(self.address)

    async def get_quote(self, balance) -> dict:
        # Получаем количество токена для вывода в wei
        def get_amount() -> float:
            bal_human = balance / (10 ** 18)
            cprint(f"На вашем счету: {bal_human} нативного токена", 'light_green')
            while True:
                try:
                    amount = float(input(colored("Введите сумму перевода нативного токена: ", 'light_green')))
                    if amount <= 0:
                        cprint(f" Пожалуйста, введите корректное число.", 'light_red')
                        continue
                    elif bal_human == 0:
                        cprint(f" На вашем счету нет токенов", 'light_red')
                        exit(1)
                    elif bal_human < amount:
                        cprint(f"Введенная сумма превышает баланс, попробуйте еще", 'light_red')
                        continue
                    return amount
                except ValueError:
                    print("Пожалуйста, введите корректное число.")

        self.params_quote = {
            'srcChainId': self.chains[self.from_chain]['id'],
            'dstChainId': self.chains[self.to_chain]['id'],
            'srcQuoteTokenAddress': ETH_MASK,
            'srcQuoteTokenAmount': int((get_amount()) * (10 ** 18)),
            'dstQuoteTokenAddress': ETH_MASK,
            'slippage': 0.5,
        }
        cprint("Получаем котировки", 'light_green')
        quote = await self.make_request(url="https://aggregator-api.xy.finance/v1/quote", params=self.params_quote)
        if quote['success']:
            return quote
        else:
            cprint(f'Ошибка при получении quote: {quote["errorMsg"]}', 'light_red')
            exit(1)

    async def get_allowance(self, quote: dict) -> dict:
        # Получаем allowance
        params_allow = {
            "chainId": self.params_quote['srcChainId'],
            "owner": self.address,
            "spender": quote['routes'][0]['contractAddress'],
            "tokenAddress": ETH_MASK
        }
        cprint("Задаем разрешение на перевод", 'light_green')
        allowance = await self.make_request(url="https://aggregator-api.xy.finance/v1/allowance", params=params_allow)
        if allowance['success']:
            return allowance
        else:
            cprint(f'Ошибка при получении allowance: {allowance["errorMsg"]}', 'light_red')
            exit(1)

    async def build_swap_tx(self, path: dict) -> dict:
        # Строим свап
        build_params = {
            'receiver': self.address,
            'bridgeProvider': path["bridgeDescription"]["provider"],
            'srcBridgeTokenAddress': path["bridgeDescription"]["srcBridgeTokenAddress"],
            'dstBridgeTokenAddress': path["bridgeDescription"]["dstBridgeTokenAddress"],
            'srcSwapProvider': path['srcSwapDescription']['provider'] if path['srcSwapDescription'] != None else None,
            'dstSwapProvider': path['dstSwapDescription']['provider'],
        }
        build_params.update(self.params_quote)
        cprint("Собираем свап-транзакцию", 'light_green')
        build_tx = await self.make_request(url="https://aggregator-api.xy.finance/v1/buildTx", params=build_params)
        return build_tx

    async def get_priotiry_fee(self) -> int:
        # Получение приоритетной комиссии.
        fee_history = await self.w3.eth.fee_history(5, 'latest', [80.0])
        non_empty_block_priority_fees = [fee[0] for fee in fee_history["reward"] if fee[0] != 0]

        divisor_priority = max(len(non_empty_block_priority_fees), 1)
        priority_fee = int(round(sum(non_empty_block_priority_fees) / divisor_priority))

        return priority_fee

    async def prepare_tx(self, build_tx: dict) -> dict:
        # Подготовка транзакции.
        transaction = {
            'chainId': await self.w3.eth.chain_id,
            'nonce': await self.w3.eth.get_transaction_count(self.address),
            'from': self.address,
            'gasPrice': int((await self.w3.eth.gas_price) * 1.25),
            'gas': int((await self.w3.eth.estimate_gas(build_tx['tx'])) * 1.5)
        }
        transaction.update(build_tx['tx'])

        if self.eip_1559:
            del transaction['gasPrice']

            base_fee = await self.w3.eth.gas_price
            max_priority_fee_per_gas = await self.get_priotiry_fee()

            if max_priority_fee_per_gas == 0:
                max_priority_fee_per_gas = base_fee

            max_fee_per_gas = int(base_fee * 1.25 + max_priority_fee_per_gas)

            transaction['maxPriorityFeePerGas'] = max_priority_fee_per_gas
            transaction['maxFeePerGas'] = max_fee_per_gas
            transaction['type'] = '0x2'

        return transaction

    async def send_transaction(
            self, transaction=None, without_gas: bool = False, need_hash: bool = True, ready_tx: bytes = None
    ):
        if ready_tx:
            tx_hash_bytes = await self.w3.eth.send_raw_transaction(ready_tx)

            cprint('Successfully sent transaction!', 'light_green')

            tx_hash_hex = self.w3.to_hex(tx_hash_bytes)
        else:
            if not without_gas:
                transaction['gas'] = int((await self.w3.eth.estimate_gas(transaction)) * 1.5)

            signed_raw_tx = self.w3.eth.account.sign_transaction(transaction, self.private_key).raw_transaction

            cprint('Successfully signed transaction!', 'light_green')

            tx_hash_bytes = await self.w3.eth.send_raw_transaction(signed_raw_tx)

            cprint('Successfully sent transaction!', 'light_green')

            tx_hash_hex = self.w3.to_hex(tx_hash_bytes)

        if need_hash:
            await self.wait_tx(tx_hash_hex)
            return tx_hash_hex

        return await self.wait_tx(tx_hash_hex)

    async def wait_tx(self, tx_hash):
        total_time = 0
        timeout = 120
        poll_latency = 10
        while True:
            try:
                receipts = await self.w3.eth.get_transaction_receipt(tx_hash)
                status = receipts.get("status")
                if status == 1:
                    cprint(f'Transaction was successful: {self.explorer_url}tx/{tx_hash}', 'light_green')
                    return True
                elif status is None:
                    await asyncio.sleep(poll_latency)
                else:
                    cprint(f'Transaction failed: {self.explorer_url}tx/{tx_hash}', 'light_red')
                    return False
            except TransactionNotFound:
                if total_time > timeout:
                    cprint(f"Transaction is not in the chain after {timeout} seconds", 'light_yellow')
                    return False
                total_time += poll_latency
                await asyncio.sleep(poll_latency)

    # async def sign_send_transaction(self, build_tx: dict) -> hex:
    #     transaction = await self.prepare_tx(build_tx)
    #     tx_hash = await self.send_transaction(transaction=transaction)
    #     return tx_hash

    async def get_status_crosschain(self, tx_hash: str) -> None:
        # Проверяем статус кросс-чейн транзакции
        total_time = 0
        timeout = 720
        poll_latency = 30
        cprint("Проверяем статус кросс-чейн транзакции", 'light_green')
        cross_params = {
            'srcChainId': self.params_quote['srcChainId'],
            'srcTxHash': tx_hash,
        }
        while True:
            response = await self.make_request(url="https://aggregator-api.xy.finance/v1/crossChainStatus",
                                               params=cross_params)
            success = response['success']
            status = response['status']
            msg = response['msg']
            tx = response['tx']
            if success:
                if status == 'Done':
                    cprint(f"{msg}\n{tx}", 'light_green')
                    break
                elif status == 'Processing':
                    cprint(f"{msg}", 'light_yellow')
                elif 'Receive bridge token' in status:
                    cprint(f"{msg}\n{tx}", 'light_red')
                    break
                elif 'Receive synapse bridge token' in status:
                    cprint(f"{msg}\n{tx}", 'light_red')
                    break
                elif 'Pending refund' in status:
                    cprint(f"{msg}", 'light_red')
                    break
                elif status == 'Refunded':
                    cprint(f"{msg}\n{tx}", 'light_red')
                    break
            else:
                cprint(f"{msg}", 'light_yellow')

            if total_time > timeout:
                cprint(f"Вышло время ожидания окончания кросс-транзакции", 'light_yellow')
                exit(1)

            total_time += poll_latency
            await asyncio.sleep(poll_latency)


async def main():
    xyf = XYFin(proxy)  # Создание экземпляра XYFin
    quote = await xyf.get_quote(await xyf.get_balance())  # Получение котировки
    path = calculate_best_path(quote)  # Ищем путь с максимальным minReceiveAmount
    allowance = await xyf.get_allowance(quote)  # Отправляем одобрение на снятие средств с нашего кошелька
    build_tx = await xyf.build_swap_tx(path)  # Собарием своп транзакцию
    transaction = await xyf.prepare_tx(build_tx)  # Готовим данные для транзакции
    tx_hash_hex = await xyf.send_transaction(transaction=transaction)  # Подписываем и отправляем транзакцию
    await xyf.get_status_crosschain(tx_hash_hex)  # Получаем кросс-чейн статус


asyncio.run(main())
