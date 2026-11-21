"""Русский / English для окна программы.

Тексты в коде написаны по-русски. При английском языке каждая строка, которую
окно показывает (подписи, кнопки, подсказки, таблицы, журнал, надписи на карте),
проходит через tr(): сначала ищется целиком, потом по кускам — самые длинные
куски заменяются первыми. Так переводятся и строки, собранные из f-строк.
Журнал в файле sstv_out/log.txt остаётся русским.
"""
from __future__ import annotations

import re

LANG = "ru"
_CYR = re.compile("[А-Яа-яЁё]")

EN = {
    # дни недели, стороны света
    "пн": "Mon", "вт": "Tue", "ср": "Wed", "чт": "Thu", "пт": "Fri", "сб": "Sat", "вс": "Sun",
    "С": "N", "В": "E", "Ю": "S", "З": "W",
    "ССВ": "NNE", "СВ": "NE", "ВСВ": "ENE", "ВЮВ": "ESE", "ЮВ": "SE", "ЮЮВ": "SSE", "ЮЮЗ": "SSW",
    "ЮЗ": "SW", "ЗЮЗ": "WSW", "ЗСЗ": "WNW", "СЗ": "NW", "ССЗ": "NNW",
    # единицы
    "МГц": "MHz", "Гц": "Hz", "Гц,": "Hz,", "кан.,": "ch,", "16 бит": "16 bit", "км": "km", "м": "m",
    "с": "s", "с)": "s)", "с.": "s.", "к": "k", "сут.": "d", "сут": "d", "сут ·": "d ·", "сут.\n": "d\n",
    "мин) …": "min) …",
    # бренд
    "SSTV · МКС": "SSTV · ISS", "SSTV МКС": "SSTV ISS", "МКС": "ISS", "МКС (ISS)": "ISS",
    "QMR-KWT-2 «Луна Кувейта» (RS95S)": "QMR-KWT-2 “Kuwait Moon” (RS95S)",
    "Правила — SSTV МКС": "Rules — SSTV ISS",
    "ЧУЖИЕ ПРИЁМНИКИ · ПРАВИЛА": "OTHER PEOPLE'S RECEIVERS · RULES",
    "Прочитал, согласен и буду соблюдать": "I have read and agree to follow these rules",
    "Выйти": "Quit", "Принять": "Accept", "Закрыть": "Close", "закрыть": "close",
    # звук
    "Звук: модуль QtMultimedia не загрузился, звука в программе не будет.":
        "Sound: the QtMultimedia module did not load, there will be no sound.",
    "Звук недоступен:": "Sound unavailable:",
    "Звук недоступен: Windows не сообщает ни одного устройства вывода.":
        "Sound unavailable: Windows reports no output devices.",
    "Звук:": "Sound:",
    "Звук недоступен: ни одно устройство не открылось —": "Sound unavailable: no device could be opened —",
    "Звук: вывод остановился (ошибка": "Sound: output stopped (error",
    "), перезапускаю.": "), restarting.",
    "Звук: ошибка воспроизведения:": "Sound: playback error:",
    "Звук включён": "Sound on", "Играет как «лучший» сигнал": "Playing as the “best” signal",
    "Звук выключен": "Sound off", "Общая громкость": "Master volume", "лучший": "best",
    "Автоматически слушать приёмник с лучшим сигналом SSTV": "Automatically listen to the receiver with the best SSTV signal",
    "звук недоступен": "sound unavailable",
    "ничего не слушаю — включи «лучший» или звук на карточке приёмника":
        "not listening to anything — turn on “best” or the sound on a receiver card",
    "Звук приёмника 300–3000 Гц, новое снизу. Синхроимпульсы SSTV — 1200 Гц,":
        "Receiver audio 300–3000 Hz, newest at the bottom. SSTV sync pulses are 1200 Hz,",
    "картинка — 1500…2300 Гц": "the picture is 1500…2300 Hz",
    # карточка приёмника
    "картинка появится, когда пойдёт SSTV": "the picture will appear when SSTV starts",
    "уровень": "level", "снимков": "images", "УГОЛ": "ELEV", "АЗИМУТ": "AZIMUTH", "ДОПЛЕР": "DOPPLER",
    "идёт · до": "in progress · until", "· макс": "· max", "пролётов нет": "no passes",
    # просмотр снимка
    "Снимок": "Image", "‹ Пред.": "‹ Prev", "След. ›": "Next ›", "Открыть папку": "Open folder",
    "Сохранить как…": "Save as…", "(время сеанса)": "(session time)", "· качество": "· quality",
    "· строк": "· lines", "· угол": "· elevation", "Сохранить снимок": "Save image",
    # шапка
    "запуск": "starting", "ожидание": "idle",
    "Эфир": "Live", "Карта": "Map", "Расписание": "Schedule", "Приёмники": "Receivers",
    "Галерея": "Gallery", "Настройки": "Settings", "Журнал": "Log",
    "следующий сеанс": "next session", "слушаю": "listening", "снимков за сеанс": "images this session",
    "авто-приём": "auto receive", "Принимать сеансы по расписанию автоматически": "Receive scheduled sessions automatically",
    "Тест 60 с": "Test 60 s",
    "Открыть приёмники, которые выше всех сейчас, на 60 с без доплера":
        "Open the receivers with the satellite highest right now, for 60 s without Doppler",
    "Пропустить": "Skip", "Пропустить ближайший сеанс": "Skip the next session",
    "Стоп": "Stop", "Остановить сеанс, тест или проверку": "Stop the session, test or check",
    "сводная картинка пролёта": "combined pass image",
    "Каждая строка берётся с того приёмника, где она принята лучше. Обновляется каждые 3 с.":
        "Each line is taken from the receiver that got it best. Updated every 3 s.",
    "ждём первых строк": "waiting for the first lines",
    "Сейчас приёмники не слушаются. Карточки появятся, когда начнётся сеанс или тест.":
        "No receivers are being listened to. Cards will appear when a session or test starts.",
    # карта
    "Приблизить": "Zoom in", "Отдалить": "Zoom out", "Весь мир": "Whole world",
    "Следить за спутником": "Follow the satellite", "спутник выше мин. угла": "satellite above min. elevation",
    "спутник над горизонтом": "satellite above horizon", "ждёт": "waiting", "не прошёл проверку": "failed the check",
    "выключен": "disabled", "трасса": "ground track", "пройдено": "passed", "зона выше мин. угла": "zone above min. elevation",
    "моё место (задаётся в настройках)": "my location (set in Settings)", "Я": "Me",
    "Видят спутник сейчас ·": "See the satellite now ·", "Спутник сейчас не над приёмниками из списка.":
        "The satellite is not above any listed receiver right now.",
    "· не прошёл проверку": "· failed the check", "· выключен": "· disabled", "· картинка в": "· picture in",
    "из": "of", "сеансов": "sessions", "пролётов выше мин. угла нет": "no passes above min. elevation",
    "считаю пролёты…": "calculating passes…", "· спутник": "· satellite", "слушаю:": "listening:",
    "не прошёл проверку:": "failed the check:", "снимков с него:": "images from it:",
    "сеансах из": "sessions out of", "· истории нет": "· no history", "Ближайшие пролёты:": "Upcoming passes:",
    "Небо над приёмником, текущий или ближайший пролёт:": "Sky above the receiver, current or next pass:",
    # расписание
    "Дата": "Date", "Время": "Time", "Длит.": "Dur.", "Приёмников": "Receivers", "Лучшие": "Best",
    "Состояние": "Status", "над моим местом": "over my location", "Макс": "Max", "Откуда → куда": "From → to",
    "Задай свои координаты во вкладке «Настройки» → «Моё место»,": "Set your coordinates in Settings → My location,",
    "и здесь появятся пролёты МКС над тобой.": "and ISS passes over you will appear here.",
    "над моим местом ·": "over my location ·", "сейчас": "now", "до": "until", "· через": "· in",
    "следующий хороший пролёт: до": "next good pass: up to", "· уже после конца SSTV": "· after SSTV ends",
    "пролётов в ближайшие 3 суток нет": "no passes in the next 3 days",
    "после окончания передач SSTV": "after the SSTV transmissions end",
    "Сеансов впереди:": "Sessions ahead:", "· до": "· until", "мин. угол": "min. elevation",
    "° · каждый сеанс начинается заранее на «начинать раньше»": "° · each session starts early by “start early”",
    "идёт": "running", "прошёл": "done", "пропущу": "will skip", "Вернуть": "Restore",
    # приёмники
    "поиск: страна, город, адрес": "search: country, city, address", "все": "all", "рабочие": "working",
    "не прошли проверку": "failed the check", "выключенные": "disabled", "добавленные вручную": "added manually",
    "видят спутник сейчас": "see the satellite now", "без картинок за 3+ сеанса": "no pictures in 3+ sessions",
    "дают картинку чаще всего": "give pictures most often",
    "Проверить выбранные": "Check selected",
    "Открыть выбранные и проверить звук и профиль 437.550": "Open the selected ones and check sound and the 437.550 profile",
    "Проверить все": "Check all", "Проверить все (≈10 минут)": "Check all (≈10 minutes)",
    "Тест выбранных": "Test selected", "Слушать выбранные 60 секунд прямо сейчас": "Listen to the selected ones for 60 seconds now",
    "Включить": "Enable", "Выключить": "Disable", "Выключить неработающие": "Disable broken",
    "Выключить все приёмники, не прошедшие проверку (по желанию — и без картинок за 3+ сеанса)":
        "Disable all receivers that failed the check (optionally also those with no pictures in 3+ sessions)",
    "Сбросить проверку": "Reset check", "Забыть результат проверки": "Forget the check result",
    "Искать приёмники": "Find receivers",
    "Собрать список заново: rx-tx.info, ReceiverBook и websdr.org (5–10 минут)":
        "Rebuild the list: rx-tx.info, ReceiverBook and websdr.org (5–10 minutes)",
    "В работе": "In use", "Приёмник": "Receiver", "Тип": "Type", "Координаты": "Coordinates", "Угол": "Elev.",
    "Проверка": "Check", "История": "History", "Добавить приёмник": "Add receiver",
    "Москва, R3XXX": "Moscow, R3XXX", "Адрес": "URL", "Широта": "Latitude", "Долгота": "Longitude",
    "Название": "Name", "Короткое имя": "Short name",
    "Координаты: градусы через точку, юг и запад — с минусом. Или щёлкни по карте.":
        "Coordinates: decimal degrees, south and west are negative. Or click on the map.",
    "Сохранить": "Save", "Новый": "New", "Убрать ручные данные": "Remove manual data",
    "Отметь приёмники галочками в первом столбце.": "Tick receivers in the first column.",
    "Проверить все приёмники? Это займёт около 10 минут.": "Check all receivers? It takes about 10 minutes.",
    "Проверка сама прервётся за 20 с до сеанса.": "The check stops by itself 20 s before a session.",
    "Включённых неработающих приёмников нет.": "There are no enabled broken receivers.",
    "приёмников, не прошедших проверку?": "receivers that failed the check?",
    "Их можно включить обратно зелёной кнопкой или фильтром «выключенные».":
        "You can enable them again with the green button or the “disabled” filter.",
    "и ещё": "and also", ", не давших ни одной картинки за 3+ сеанса": " that gave no picture in 3+ sessions",
    "Тест": "Test", "Выбрано": "Selected",
    ", но одновременно программа слушает не больше 12 приёмников.":
        ", but the program listens to at most 12 receivers at a time.",
    "Откроются 12 из выбранных, над которыми спутник сейчас выше.":
        "The 12 selected ones with the satellite highest right now will be opened.",
    "Обновление списка": "Update list",
    "Искать приёмники на rx-tx.info, ReceiverBook и websdr.org? Откроется окно сайта,":
        "Search for receivers on rx-tx.info, ReceiverBook and websdr.org? A site window will open,",
    "проверка всех найденных займёт 5–10 минут. Прежние приёмники не теряются.":
        "checking everything found takes 5–10 minutes. Existing receivers are kept.",
    "· вручную": "· manual", "· изменён": "· edited", "· встроенный": "· built-in",
    "работает": "works", "не проверялся": "not checked", "Картинка в": "Picture in", ", снимков": ", images",
    "рейтинг": "rating", "Ещё ни разу не слушали в сеансе": "Never listened to in a session yet",
    "вкл": "on", "выкл": "off", "Используется в сеансах — нажми, чтобы выключить": "Used in sessions — click to disable",
    "Выключен — нажми, чтобы включить": "Disabled — click to enable", "Приёмник:": "Receiver:",
    "Удалить приёмник": "Delete receiver", "Вернуть исходные данные": "Restore original data",
    "Ближайшие пролёты над ним:": "Upcoming passes over it:",
    "Координаты: широта −90…90, долгота −180…180.": "Coordinates: latitude −90…90, longitude −180…180.",
    "Нужен адрес приёмника.": "The receiver URL is required.",
    "Сохранено. Расписание пересчитается": "Saved. The schedule will be recalculated", "после сеанса.": "after the session.",
    # галерея
    "лучшее": "best", "в эфире": "on air", "пересборка": "re-decode", "склейки": "merges", "приёмник": "receiver",
    "по времени": "by time", "по качеству": "by quality", "Пересобрать записи": "Re-decode recordings",
    "Пересобрать все записи нынешним декодером": "Re-decode all recordings with the current decoder",
    "Пересборка": "Re-decode", "Пересобрать все записи нынешним декодером?": "Re-decode all recordings with the current decoder?",
    "Это займёт несколько минут; старые картинки не удаляются.": "This takes a few minutes; old pictures are not deleted.",
    "Отчёт сеанса": "Session report", "с картинкой": "with a picture", "· картинок": "· pictures", "· склеек": "· merges",
    "Макс. угол": "Max elev.", "Слушал": "Listened", "SSTV до": "SSTV up to", "Картинок": "Pictures",
    "Лучшая, строк": "Best, lines", "все спутники": "all satellites", "снимков:": "images:",
    "Снимков пока нет.": "No images yet.", "Сеанс": "Session", "· снимков": "· images", ", с картинкой": ", with a picture",
    "отчёт": "report", "склейка": "merge", "· кач.": "· q.",
    # настройки
    "Спутник": "Satellite", "другой…": "other…",
    "Спутники с SSTV в режиме Robot 36. Кроме МКС, все передают картинки эпизодически —":
        "Satellites with Robot 36 SSTV. Apart from the ISS, all of them transmit only occasionally —",
    "расписания смотри в Libre Space Community и AMSAT-BB.": "check schedules on the Libre Space Community and AMSAT-BB.",
    "Готовый": "Preset", "TLE вручную": "Manual TLE",
    "Если TLE вписан, он используется вместо скачивания. Пусто — TLE берётся из сети по NORAD ID.":
        "If a TLE is entered, it is used instead of downloading. Empty — the TLE is downloaded by NORAD ID.",
    "Скачать TLE заново": "Download TLE again", "Скачиваю TLE…": "Downloading TLE…", "Сигнал": "Signal",
    "Частота": "Frequency", "Режим SSTV": "SSTV mode",
    "Список приёмников проверялся на 437.550 МГц. На другой частоте у части OpenWebRX":
        "The receiver list was checked on 437.550 MHz. On another frequency some OpenWebRX",
    "может не оказаться подходящего профиля.": "may have no suitable profile.",
    "Приём": "Reception",
    "Не больше 12: картинок от большего числа не прибавляется, а чужим приёмникам — нагрузка.":
        "No more than 12: more does not give more pictures, but loads other people's receivers.",
    "Каждый приёмник — отдельная скрытая страница браузера, примерно 100–150 МБ памяти":
        "Each receiver is a separate hidden browser page, about 100–150 MB of memory",
    "не переключать диапазон на OpenWebRX": "do not switch the band on OpenWebRX",
    "скрывать окна приёмников": "hide receiver windows", "принимать сеансы автоматически": "receive sessions automatically",
    "при выборе приёмников учитывать, давали ли они картинку раньше": "prefer receivers that gave pictures before",
    "не больше 12 — чтобы не мешать владельцам и слушателям чужих приёмников":
        "at most 12 — so as not to disturb owners and listeners of other people's receivers",
    "Приёмников одновременно": "Receivers at once", "Мин. угол спутника": "Min. satellite elevation",
    "Не брать ближе друг к другу": "Minimum distance between them", "Начинать раньше": "Start early",
    "Слушать после": "Keep listening after", "Вернуть диапазон через": "Restore the band after", "Работать до": "Run until",
    "Моё место": "My location", "показывать моё место на карте (зелёная точка)": "show my location on the map (green dot)",
    "Подпись": "Label", "Высота": "Altitude",
    "Градусы через точку, юг и запад — с минусом. Можно щёлкнуть по карте ниже.":
        "Decimal degrees, south and west are negative. You can click on the map below.",
    "Пустые поля — место не задано. Пролёты над тобой — во вкладке «Расписание».":
        "Empty fields — no location set. Passes over you are in the Schedule tab.",
    "скрыть с карты приёмники, не прошедшие проверку": "hide receivers that failed the check from the map",
    "Действует сразу, кнопку «Сохранить» нажимать не нужно.": "Takes effect immediately, no need to press Save.",
    "О программе": "About", "версия": "version", "Автор:": "Author:", "Исходники:": "Source code:",
    "Ошибки и пожелания:": "Bugs and suggestions:", "написать на GitHub": "write on GitHub",
    "Карта: Natural Earth. Шрифты: PT Sans Narrow,": "Map: Natural Earth. Fonts: PT Sans Narrow,",
    "JetBrains Mono (SIL OFL). Орбиты: CelesTrak.": "JetBrains Mono (SIL OFL). Orbits: CelesTrak.",
    "Флаги: flag-icons (MIT).": "Flags: flag-icons (MIT).",
    "Правила и отказ от ответственности": "Rules and disclaimer",
    "Язык": "Language",
    "Применить спутник": "Apply satellite",
    "Сохранить только спутник и частоту — без кнопки «Сохранить» внизу":
        "Save only the satellite and frequency — no need for the Save button at the bottom",
    "Без изменений: этот спутник уже выбран.": "No changes: this satellite is already selected.",
    "Сохраняю…": "Saving…", "Сохранено. Спутник сменится после сеанса.": "Saved. The satellite will change after the session.",
    "Считаю расписание…": "Calculating the schedule…",
    "Не получилось:": "Failed:", "Готово:": "Done:", "сеансов": "sessions",
    "скачиваю TLE": "downloading TLE", "считаю расписание": "calculating the schedule",
    "Сейчас:": "Now:", ", возраст": ", age", "км, над точкой": "km, over", "TLE не распознан:": "TLE not recognised:",
    "NORAD ID — целое число.": "NORAD ID must be an integer.",
    "Моё место: нужны и широта, и долгота (или оба поля пустые).": "My location: both latitude and longitude are needed (or both empty).",
    "Моё место: широта −90…90, долгота −180…180.": "My location: latitude −90…90, longitude −180…180.",
    "Сохранено": "Saved", ". Спутник и расписание сменятся после сеанса.": ". The satellite and schedule will change after the session.",
    "Выход": "Quit", "Идёт приём. Остановить его и выйти? Записанное сохранится и склеится.":
        "Reception is in progress. Stop it and quit? What was recorded will be saved and merged.",
    # состояние в шапке
    "сеанс": "session", "тест": "test", "проверка": "check", "поиск": "search", "расчёт": "calculating",
    "орбита не загружена": "orbit not loaded", "Источник TLE:": "TLE source:", "· осталось": "· left",
    "· приёмников": "· receivers", "· авто-приём выключен": "· auto receive off",
    "сеансов до конца срока нет": "no sessions until the end date",
    "высота": "altitude", "через": "in", "пр.": "rx", "видят спутник:": "see the satellite:",
    "всего в галерее:": "in the gallery:", "Сейчас слушаю:": "Listening now:", "Лучшие в ближайшем сеансе:": "Best in the next session:",
    "Ошибка интерфейса:": "Interface error:", "(подробности в sstv_out/console.log)": "(details in sstv_out/console.log)",
    # журнал: engine.py
    "Не удалось сохранить настройки:": "Could not save settings:", "Орбита не загружена:": "Orbit not loaded:",
    "Расписание: сеансов": "Schedule: sessions", "Отчёт не записан:": "Report not written:",
    "Скоро сеанс — прерываю проверку.": "A session is coming — stopping the check.",
    "живая склейка:": "live merge:", "приёмников:": "receivers:", "(идёт приём)": "(receiving)",
    "· другие куски не совместились": "· other pieces did not align",
    "): приёмников": "): receivers", ", с картинками": ", with pictures", "картинок": "pictures", ", склеек": ", merges",
    "угол до": "elevation up to", "°  слушал": "°  listened", "%  картинок": "%  pictures", ", лучшая": ", best",
    "строк": "lines", "Ошибка команды": "Command error", "широта вне диапазона": "latitude out of range",
    "долгота вне диапазона": "longitude out of range", "Настройки сохранены": "Settings saved",
    "— пересчитываю расписание": "— recalculating the schedule", "координаты вне диапазона": "coordinates out of range",
    "Приёмник сохранён:": "Receiver saved:", "Ручные данные приёмника удалены.": "Manual receiver data removed.",
    "Сейчас идёт другая работа — дождись её окончания или нажми «Стоп».":
        "Something else is running — wait for it to finish or press Stop.",
    "До сеанса меньше 15 минут (": "Less than 15 minutes to the session (", "пересборку": "the re-decode",
    "лучше запустить после него.": "better run it after the session.",
    "Тест: нет подходящих приёмников.": "Test: no suitable receivers.", "Тест:": "Test:",
    "приёмников на": "receivers for", "с, без доплера.": "s, without Doppler.", "Ошибка:": "Error:",
    "Ошибка запуска:": "Start error:", "Сеанс прерван ошибкой:": "Session aborted by error:", "Ошибка ядра:": "Core error:",
    "открываю": "opening", "уступил": "yielded", "нет звука": "no sound", "картинка": "picture",
    "склейка нескольких приёмников": "merge of several receivers", "Goonhilly, Корнуолл": "Goonhilly, Cornwall",
    # журнал: iss_sstv.py
    "нет профиля с 437.550": "no profile with 437.550",
    "тест: приёмник на другом диапазоне (": "test: receiver is on another band (",
    "), не переключаю, проверяю только звук": "), not switching, checking sound only",
    "приёмник на другом диапазоне (": "receiver is on another band (", "), не переключаю": "), not switching",
    "профиль переключён на «": "profile switched to «", "профиль не переключился": "profile did not switch",
    "пресета нет: частота и FM выставлены вручную": "no preset: frequency and FM set manually",
    "не найдено поле частоты": "frequency field not found",
    "введён вручную": "entered manually",
    "ВНИМАНИЕ: TLE старше 3 суток, время пролётов может уйти на минуту и больше.":
        "WARNING: the TLE is older than 3 days, pass times may drift by a minute or more.",
    "страница не загрузилась": "page did not load", "страница не настроилась": "page did not set up",
    "не удалось настроить приёмник": "could not set up the receiver", "переключён": "switched",
    ": кто-то сменил диапазон — верну 437.550": ": someone changed the band — will restore 437.550",
    ": вернул 437.550 (": ": restored 437.550 (", ": открываю": ": opening", ": звук идёт": ": sound is coming",
    ": звук пропал на": ": sound lost for", "с — переоткрываю страницу (раз": "s — reopening the page (attempt",
    ": звука нет. Если окно браузера видно, нажми на странице запуск звука.":
        ": no sound. If the browser window is visible, press the start-audio button on the page.",
    ": ошибка при дорисовке:": ": error while finishing the picture:", ": закончил, картинок": ": finished, pictures",
    "-- к": "-- to", "жду": "waiting", "приёмников": "receivers", "; лучшие:": "; best:",
    "Сеанс остановлен из интерфейса.": "Session stopped from the interface.", ": не открылся (": ": did not open (",
    ": звук всё время пропадает, закрываю.": ": sound keeps dropping, closing.", ": ошибка (": ": error (",
    "), закрываю.": "), closing.", "нет профиля": "no profile", "пр., карт": "rx, pics", ", конец через": ", ends in",
    "Остановлено.": "Stopped.", "Склейка не удалась:": "Merge failed:",
    "Сеанс закончен: приёмников слушали": "Session finished: receivers listened", ", картинок": ", pictures",
    "склеек": "merges", ". Всё в": ". Everything is in",
    ": помечен как нерабочий, больше открывать не буду.": ": marked as broken, will not open it again.",
    "Готово, картинок:": "Done, pictures:",
    "Списка receivers.json нет — работаю с 5 проверенными приёмниками.": "No receivers.json list — using the 5 verified receivers.",
    "Запусти с --scan, чтобы найти остальные.": "Run with --scan to find the rest.",
    "Нет ни одного приёмника.": "There are no receivers.", "Тест: открываю до": "Test: opening up to",
    "приёмников на 60 секунд, 437.550 без доплера.": "receivers for 60 seconds, 437.550 without Doppler.",
    "Подходящих пролётов до конца эксперимента нет.": "No suitable passes until the end of the event.",
    "Приёмников в списке:": "Receivers in the list:", "(не прошли проверку:": "(failed the check:",
    ". Сеансов до конца эксперимента:": ". Sessions until the end of the event:", "Следующий сеанс": "Next session",
    "); старт через": "); starts in", "(Ctrl+C — выход)": "(Ctrl+C — quit)",
    "Обновляю орбиту МКС (TLE) и расписание …": "Updating the orbit (TLE) and schedule …",
    "Расписание закончилось.": "The schedule is over.", "Проверяю": "Checking",
    "приёмников пачками по": "receivers in batches of", "(примерно": "(about",
    "звук пропадает или одна тишина": "sound drops or is just silence", "Проверено": "Checked",
    "звук не пошёл": "no sound came", "не настроился за 45 с": "did not set up within 45 s",
    "Готово: работают": "Done: working", ". Результат в": ". Result in",
    "Не прошедшие проверку приёмники в сеансах пропускаются (кроме пяти встроенных).":
        "Receivers that failed the check are skipped in sessions (except the five built-in ones).",
    "Выход.": "Quit.",
    # scan.py
    "YO5BWS, Клуж-Напока": "YO5BWS, Cluj-Napoca", "Волоколамск": "Volokolamsk", "Москва, ЗАО": "Moscow, ZAO",
    "R9SBY, Оренбург": "R9SBY, Orenburg", "Москва": "Moscow",
    "ReceiverBook: список не найден на странице.": "ReceiverBook: list not found on the page.",
    "ReceiverBook: приёмников": "ReceiverBook: receivers", "websdr.org: список не получен (": "websdr.org: list not received (",
    "websdr.org: WebSDR с диапазоном 437.550:": "websdr.org: WebSDRs covering 437.550:",
    "На карте rx-tx.info точек:": "Points on the rx-tx.info map:",
    "Карта не отдала точки (возможно, сайт попросил проверку «я не робот»).":
        "The map returned no points (the site may have asked for an “I'm not a robot” check).",
    "Запусти --scan ещё раз и, если в окне появится проверка, пройди её.":
        "Run --scan again and, if a check appears in the window, complete it.",
    "Карту rx-tx.info загрузить не удалось (": "Could not load the rx-tx.info map (",
    "OpenWebRX (status.json, займёт несколько минут) …": "OpenWebRX (status.json, takes a few minutes) …",
    "проверено": "checked", ", подходят": ", suitable", "OpenWebRX живых и с профилем на 437.550:": "OpenWebRX alive with a 437.550 profile:",
    "Из прежнего списка оставлено": "Kept from the previous list", "(сейчас не ответили или не найдены)": "(did not respond or not found now)",
    "Сохранено в": "Saved to", "(новых": "(new",
    # tle.py
    "TLE не найден": "TLE not found", "для": "for", "кэш (": "cache (", "контрольная сумма": "checksum",
    "TLE не скачан (": "TLE not downloaded (", "старый кэш (": "old cache (", "встроенный": "built-in",
    "Нет TLE для NORAD": "No TLE for NORAD",
    # redecode.py, combine.py, robot36.py
    ": не читается (": ": unreadable (", ": картинка": ": picture", "строк, качество": "lines, quality",
    ", начало пропущено": ", start missed", "склейка не удалась:": "merge failed:", "Готово: картинок": "Done: pictures",
    ". Лучшие — в": ". The best are in", ": нет общих строк, не склеиваю": ": no common lines, not merging",
    ": не уверен в совмещении (корр.": ": alignment uncertain (corr.", "), не склеиваю": "), not merging",
    ": сдвиг": ": shift", "строк, корр.": "lines, corr.", "усреднено строк:": "lines averaged:", "Картинка": "Picture",
    ": приёмники": ": receivers", "Склейка": "Merge", ": строк из каждого приёмника —": ": lines from each receiver —",
    "строк, с": "lines, from", "с, качество": "s, quality", "скорость": "speed",
    ", начало пропущено (кусок прижат к": ", start missed (piece aligned to the", "низу": "bottom", "верху": "top",
    "Картинка сохранена:": "Picture saved:", "строк, скорость звука": "lines, audio speed",
}

RULES_HTML_EN = """
<p style='font-size:14px'>Online receivers are run by radio amateurs — <b>free, for everyone</b>.
An OpenWebRX has <b>one tuner shared by all listeners</b>: when the program switches a receiver to 70 cm,
everyone listening to it right now ends up on 70 cm too.</p>
<p style='font-size:14px'><b>Rules:</b></p>
<ul style='font-size:14px'>
<li><b>8–12 receivers at once</b> are enough to follow a pass from horizon to horizon.
More gives almost nothing, but disturbs many people at once and loads other people's links,
so the program will not open more than 12.</li>
<li>Connect only <b>for the duration of a pass</b> — the program does exactly that by itself.</li>
<li>If a receiver's owner asks not to switch the band, or limits time
and the number of listeners — <b>disable that receiver</b> in the Receivers tab.</li>
<li>Run “Check all” and the receiver search before an event, not all the time.</li>
<li>Don't want to touch other people's settings at all — turn on
“do not switch the band on OpenWebRX” in Settings.</li>
</ul>
<p style='font-size:14px'><b>Disclaimer.</b> The program is provided “as is”, without any warranty.
None of the receivers belongs to the author. <b>You are responsible for how you use it</b>:
for the number of connections, switching the band for other listeners, following the rules of receivers,
directories and the laws of your country. The author is not liable for any damage, bans or claims
arising from the use of the program.</p>
"""

_rx = None
_cache: dict = {}
_B = "А-Яа-яЁё"
_ATTR = re.compile(r"""((?:src|href)=(?:'[^']*'|"[^"]*"))""")


def _build():
    global _rx
    keys = sorted(EN, key=len, reverse=True)
    # граница слова проверяется только с той стороны, где у куска буква
    _rx = re.compile("|".join((f"(?<![{_B}])" if _CYR.match(k[0]) else "") + re.escape(k)
                              + (f"(?![{_B}])" if _CYR.match(k[-1]) else "") for k in keys))


def set_lang(lang):
    global LANG
    LANG = "en" if lang == "en" else "ru"
    _cache.clear()


def tr(s):
    """Строка на текущем языке. Не строки и строки без кириллицы возвращаются как есть."""
    if LANG == "ru" or not isinstance(s, str) or not _CYR.search(s):
        return s
    r = _cache.get(s)
    if r is not None:
        return r
    r = EN.get(s)
    if r is None:
        if _rx is None:
            _build()
        # пути и ссылки в src='…' / href='…' не трогаем: в них может быть кириллица из имени папки
        r = "".join(x if i % 2 else _rx.sub(lambda m: EN[m.group(0)], x) for i, x in enumerate(_ATTR.split(s)))
    if len(_cache) > 20000:
        _cache.clear()
    _cache[s] = r
    return r


# ---------------- Qt: все тексты окна идут через tr() ----------------
_installed = False


def install_qt():
    """Подменяет у Qt методы, которыми окно ставит тексты. Вызывать до создания окон."""
    global _installed
    if _installed:
        return
    _installed = True
    from PyQt5 import QtCore, QtGui, QtWidgets

    def wrap1(cls, name, pos=0):
        orig = getattr(cls, name)

        def f(self, *a, **k):
            if len(a) > pos and isinstance(a[pos], str):
                a = a[:pos] + (tr(a[pos]),) + a[pos + 1:]
            return orig(self, *a, **k)
        setattr(cls, name, f)

    for cls, names in (
            (QtWidgets.QLabel, ("setText",)),
            (QtWidgets.QAbstractButton, ("setText",)),
            (QtWidgets.QWidget, ("setToolTip", "setWindowTitle")),
            (QtWidgets.QLineEdit, ("setPlaceholderText",)),
            (QtWidgets.QGroupBox, ("setTitle",)),
            (QtWidgets.QTableWidgetItem, ("setText", "setToolTip")),
            (QtWidgets.QAbstractSpinBox, ("setSpecialValueText",)),
            (QtWidgets.QSpinBox, ("setSuffix", "setPrefix")),
            (QtWidgets.QDoubleSpinBox, ("setSuffix", "setPrefix")),
            (QtWidgets.QPlainTextEdit, ("appendPlainText",)),
            (QtWidgets.QTextBrowser, ("setHtml",)),
            (QtWidgets.QMessageBox, ("setText", "setInformativeText")),
            (QtWidgets.QProgressBar, ("setFormat",)),
            (QtWidgets.QComboBox, ("addItem",)),
            (QtWidgets.QAction, ("setText", "setToolTip"))):
        for n in names:
            wrap1(cls, n)
    wrap1(QtWidgets.QTabWidget, "addTab", 1)
    wrap1(QtWidgets.QTabWidget, "setTabText", 1)
    wrap1(QtWidgets.QTabWidget, "setTabToolTip", 1)
    wrap1(QtWidgets.QComboBox, "setItemText", 1)

    for cls, name in ((QtWidgets.QTableWidget, "setHorizontalHeaderLabels"),
                      (QtWidgets.QTableWidget, "setVerticalHeaderLabels"),
                      (QtWidgets.QComboBox, "addItems")):
        orig = getattr(cls, name)

        def f(self, items, *a, _o=orig):
            return _o(self, [tr(x) for x in items], *a)
        setattr(cls, name, f)

    o_addrow = QtWidgets.QFormLayout.addRow

    def addRow(self, *a):  # noqa: N802
        if a and isinstance(a[0], str):
            a = (tr(a[0]),) + a[1:]
        return o_addrow(self, *a)
    QtWidgets.QFormLayout.addRow = addRow

    o_draw = QtGui.QPainter.drawText

    def drawText(self, *a):  # noqa: N802
        if a and isinstance(a[-1], str):
            a = a[:-1] + (tr(a[-1]),)
        elif len(a) >= 3 and isinstance(a[2], str):      # (rect, flags, text, …)
            a = a[:2] + (tr(a[2]),) + a[3:]
        return o_draw(self, *a)
    QtGui.QPainter.drawText = drawText

    o_tip = QtWidgets.QToolTip.showText

    def showText(pos, text, *a):  # noqa: N802
        return o_tip(pos, tr(text), *a)
    QtWidgets.QToolTip.showText = staticmethod(showText)

    for n in ("information", "question", "warning", "critical"):
        orig = getattr(QtWidgets.QMessageBox, n)

        def f(parent, title, text, *a, _o=orig):
            return _o(parent, tr(title), tr(text), *a)
        setattr(QtWidgets.QMessageBox, n, staticmethod(f))

    o_save = QtWidgets.QFileDialog.getSaveFileName

    def getSaveFileName(parent=None, caption="", *a, **k):  # noqa: N802
        return o_save(parent, tr(caption), *a, **k)
    QtWidgets.QFileDialog.getSaveFileName = staticmethod(getSaveFileName)

    # конструкторы с текстом: QLabel("…"), QPushButton("…"), QTableWidgetItem("…") и т.п.
    def sub(base):
        class T(base):
            def __init__(self, *a, **k):
                if a and isinstance(a[0], str):
                    a = (tr(a[0]),) + a[1:]
                super().__init__(*a, **k)
        T.__name__ = base.__name__
        T.__qualname__ = base.__qualname__
        return T

    for n in ("QLabel", "QPushButton", "QCheckBox", "QRadioButton", "QGroupBox", "QToolButton", "QTableWidgetItem"):
        setattr(QtWidgets, n, sub(getattr(QtWidgets, n)))
    _ = QtCore
