import aiohttp
import asyncio
import re


class HHParser:
    def __init__(self, access_token):
        self.base_url = "https://api.hh.ru/vacancies"
        self.access_token = access_token
        self.headers = {
            "User-Agent": "VacancySearchApp/1.0 (chipsplayrus@gmail.com)",
            "Authorization": f"Bearer {self.access_token}"
        }
        # Уменьшим одновременные запросы, чтобы меньше злить защиту HH
        self.semaphore = asyncio.Semaphore(5)

    def _clean_html(self, raw_html):
        if not raw_html: return "Не указано"
        clean_re = re.compile('<.*?>')
        return re.sub(clean_re, '', raw_html).replace('&nbsp;', ' ').replace('&quot;', '"').strip()

    async def get_full_description(self, session, vacancy_id, retries=3):
        detail_url = f"{self.base_url}/{vacancy_id}"

        # Делаем несколько попыток в случае сбоя сети
        for attempt in range(retries):
            async with self.semaphore:
                try:
                    # ssl=False отсюда убрали, так как он задан в TCPConnector
                    async with session.get(detail_url, headers=self.headers, timeout=10) as response:
                        if response.status == 200:
                            data = await response.json()
                            return self._clean_html(data.get('description', ''))
                        elif response.status == 429:
                            # Если словили лимит, ждем подольше
                            await asyncio.sleep(2)
                            continue
                except Exception as e:
                    if attempt == retries - 1:
                        return f"Ошибка загрузки деталей: {e}"
                    # Небольшая пауза перед следующей попыткой
                    await asyncio.sleep(1)

        return "Описание не найдено (превышено число попыток)"

    async def get_vacancies(self, query, pages=1):
        all_results = []

        # force_close=True не дает использовать "протухшие" соединения
        # limit_per_host ограничивает количество коннектов к одному хосту
        connector = aiohttp.TCPConnector(
            ssl=False,
            force_close=True,
            limit_per_host=10
        )

        async with aiohttp.ClientSession(connector=connector) as session:
            for page in range(pages):
                params = {
                    "text": query,
                    "area": 113,
                    "per_page": 20,
                    "page": page,
                    "only_with_salary": 1
                }

                try:
                    async with session.get(self.base_url, headers=self.headers, params=params) as resp:
                        if resp.status != 200:
                            err_text = await resp.text()
                            print(f"Ошибка API: {resp.status} - {err_text}")
                            return f"Ошибка API: {resp.status}"

                        data = await resp.json()
                        items = data.get('items', [])
                        if not items: break

                        tasks = [self.get_full_description(session, item.get('id')) for item in items]
                        descriptions = await asyncio.gather(*tasks)

                        for item, desc in zip(items, descriptions):
                            all_results.append(self._process_vacancy(item, desc))

                        # Пауза между страницами для имитации человеческого поведения
                        await asyncio.sleep(0.5)

                except Exception as e:
                    print(f"Ошибка на странице {page}: {e}")
                    return f"Ошибка соединения: {e}"

        return all_results

    def _process_vacancy(self, item, full_desc):
        salary = item.get('salary', {})
        return {
            "Название": item.get('name'),
            "Компания": item.get('employer', {}).get('name'),
            "Зарплата ОТ": salary.get('from') if salary else None,
            "Зарплата ДО": salary.get('to') if salary else None,
            "Валюта": salary.get('currency') if salary else "",
            "Город": item.get('area', {}).get('name'),
            "Опыт": item.get('experience', {}).get('name'),
            "Полное описание": full_desc,
            "Ссылка": item.get('alternate_url')
        }