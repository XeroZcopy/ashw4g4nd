import asyncio
from telethon import TelegramClient

# Подставь свои данные именно этого аккаунта
API_ID = 36728718
API_HASH = "8497ffb8eb82166e1b355430993d42cd"
SESSION_NAME = "Попкорн" 

async def main():
    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
    await client.start()
    print("Авторизация успешна! Файл сессии создан.")
    await client.disconnect()

if __name__ == '__main__':
    asyncio.run(main())
