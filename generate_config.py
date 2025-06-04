import os

def generate_config():
    caldav_url = os.getenv("CALDAV_URL", "url")
    caldav_username = os.getenv("CALDAV_USERNAME", "username")
    caldav_password = os.getenv("CALDAV_PASSWORD", "password")

    config_content = f"""import os

CALDAV_URL = os.getenv("CALDAV_URL", "{caldav_url}")
CALDAV_USERNAME = os.getenv("CALDAV_USERNAME", "{caldav_username}")
CALDAV_PASSWORD = os.getenv("CALDAV_PASSWORD", "{caldav_password}")
SQLALCHEMY_DATABASE_URI = 'sqlite:///tasks.db'
SQLALCHEMY_TRACK_MODIFICATIONS = False
"""

    with open("config.py", "w", encoding="utf-8") as f:
        f.write(config_content)
    print("config.py has been generated.")

if __name__ == "__main__":
    generate_config()
