WB_SEARCH_URL = (
    'https://search.wb.ru/exactmatch/ru/common/v9/search'
)

WB_HEADERS: dict[str, str] = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/125.0.0.0 Safari/537.36'
    ),
    'Accept': '*/*',
    'Accept-Language': 'ru-RU,ru;q=0.9',
}
