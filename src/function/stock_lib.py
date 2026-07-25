import datetime
import pprint
import requests
import yaml
from yahoo_finance_api2.exceptions import YahooFinanceError

PERIOD_TYPE_DAY = "day"
PERIOD_TYPE_WEEK = "week"
PERIOD_TYPE_MONTH = "month"
PERIOD_TYPE_YEAR = "year"

# Valid frequencies: [1m, 2m, 5m, 15m, 30m, 60m, 90m, 1h, 1d, 5d, 1wk, 1mo, 3mo]
FREQUENCY_TYPE_MINUTE = "m"
FREQUENCY_TYPE_HOUR = "h"
FREQUENCY_TYPE_DAY = "d"
FREQUENCY_TYPE_WEEK = "wk"
FREQUENCY_TYPE_MONTH = "mo"


class StockLib(object):

    def __init__(self, symbol):
        self.symbol = symbol

    def get_historical(self, driver, period_type, period, frequency_type, frequency):
        # data = self._download_symbol_data(period_type, period,
        #                                  frequency_type, frequency)

        data = self._download_symbol_data_by_selenium2(
            driver, period_type, period, frequency_type, frequency
        )
        if data is None:
            return None
        valid_frequency_types = [
            FREQUENCY_TYPE_MINUTE,
            FREQUENCY_TYPE_HOUR,
            FREQUENCY_TYPE_DAY,
            FREQUENCY_TYPE_WEEK,
            FREQUENCY_TYPE_MONTH,
        ]

        if frequency_type not in valid_frequency_types:
            raise ValueError("Invalid frequency type: " % frequency_type)

        if "timestamp" not in data:
            return None

        return_data = {
            "timestamp": [x * 1000 for x in data["timestamp"]],
            "open": data["indicators"]["quote"][0]["open"],
            "high": data["indicators"]["quote"][0]["high"],
            "low": data["indicators"]["quote"][0]["low"],
            "close": data["indicators"]["quote"][0]["close"],
            "volume": data["indicators"]["quote"][0]["volume"],
        }

        return return_data

    def _set_time_frame(self, period_type, period):
        now = datetime.datetime.now()

        if period_type == PERIOD_TYPE_DAY:
            period = min(period, 59)
            start_time = now - datetime.timedelta(days=period)
        elif period_type == PERIOD_TYPE_WEEK:
            period = min(period, 59)
            start_time = now - datetime.timedelta(days=period * 7)
        elif period_type == PERIOD_TYPE_MONTH:
            period = min(period, 59)
            start_time = now - datetime.timedelta(days=period * 30)
        elif period_type == PERIOD_TYPE_YEAR:
            period = min(period, 59)
            start_time = now - datetime.timedelta(days=period * 365)
        else:
            raise ValueError("Invalid period type: " % period_type)

        end_time = now

        return int(start_time.timestamp()), int(end_time.timestamp())

    def _download_symbol_data(self, period_type, period, frequency_type, frequency):
        start_time, end_time = self._set_time_frame(period_type, period)
        url = (
            "https://query1.finance.yahoo.com/v8/finance/chart/{0}?symbol={0}"
            "&period1={1}&period2={2}&interval={3}&"
            "includePrePost=true&events=div%7Csplit%7Cearn&lang=en-US&"
            "region=US&crumb=t5QZMhgytYZ&corsDomain=finance.yahoo.com"
        ).format(
            self.symbol,
            start_time,
            end_time,
            self._frequency_str(frequency_type, frequency),
        )

        # print(url)

        # headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'}
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0"
        }
        # setting timeout
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        resp_json = response.json()

        if self._is_yf_response_error(resp_json):
            self._raise_yf_response_error(resp_json)
            return

        data_json = resp_json["chart"]["result"][0]

        return data_json

    # https://googlechromelabs.github.io/chrome-for-testing/known-good-versions-with-downloads.json
    def _download_symbol_data_by_selenium(
        self, period_type, period, frequency_type, frequency
    ):
        from selenium import webdriver
        from selenium.webdriver.chrome.service import Service
        from selenium.webdriver.common.by import By
        import time

        start_time, end_time = self._set_time_frame(period_type, period)
        url = (
            "https://query1.finance.yahoo.com/v8/finance/chart/{0}?symbol={0}"
            "&period1={1}&period2={2}&interval={3}&"
            "includePrePost=true&events=div%7Csplit%7Cearn&lang=en-US&"
            "region=US&crumb=t5QZMhgytYZ&corsDomain=finance.yahoo.com"
        ).format(
            self.symbol,
            start_time,
            end_time,
            self._frequency_str(frequency_type, frequency),
        )

        # service = Service("chromedriver.exe")
        # driver = webdriver.Chrome(service=service)
        driver = webdriver.Chrome()
        driver.get(url)

        html = driver.page_source
        driver.quit()
        time.sleep(100000)
        return html

    def _download_symbol_data_by_selenium2(
        self, driver, period_type, period, frequency_type, frequency
    ):
        from selenium.webdriver.common.by import By
        import json
        import time

        start_time, end_time = self._set_time_frame(period_type, period)
        url = (
            "https://query1.finance.yahoo.com/v8/finance/chart/{0}?symbol={0}"
            "&period1={1}&period2={2}&interval={3}&"
            "includePrePost=true&events=div%7Csplit%7Cearn&lang=en-US&"
            "region=US&crumb=t5QZMhgytYZ&corsDomain=finance.yahoo.com"
        ).format(
            self.symbol,
            start_time,
            end_time,
            self._frequency_str(frequency_type, frequency),
        )

        count = 0
        while True:
            try:
                driver.get(url)
                j = driver.find_element(By.CSS_SELECTOR, "pre")
                data = json.loads(j.text)
                return data["chart"]["result"][0]
            except Exception as e:
                print("Error: ", e)
                # print("Retrying...")
                time.sleep(5)
            count += 1
            if count >= 3:
                break
        return None

    def _is_yf_response_error(self, resp):
        return resp["chart"]["error"] is not None

    def _raise_yf_response_error(self, resp):
        raise YahooFinanceError(
            "{0}: {1}".format(
                resp["chart"]["error"]["code"], resp["chart"]["error"]["description"]
            )
        )

    def _frequency_str(self, frequency_type, frequency):
        return "{1}{0}".format(frequency_type, frequency)
