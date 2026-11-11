"""Сведения о программе: версия, автор, ссылка для обратной связи.

Заполни AUTHOR и REPO_URL — они появятся в заголовке окна и в разделе
«О программе» во вкладке «Настройки». Пустые поля просто не показываются.
"""
__version__ = "1.0.0"

APP_NAME = "SSTV · МКС — пульт приёма"
AUTHOR = "smrplugg"
REPO_URL = "https://github.com/smrplugg/iss-sstv-receiver"


def issues_url():
    return REPO_URL.rstrip("/") + "/issues" if REPO_URL else ""
