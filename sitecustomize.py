import os

# CROCODILLE RENT – interval automatické obnovy externích dat.
# Python načítá sitecustomize automaticky při startu procesu, takže se tyto
# výchozí hodnoty nastaví ještě před importem app.py a service_fleet.py.
WEEK_SECONDS = str(7 * 24 * 60 * 60)

# STK / obecná cache v app.py: všechna již ověřená data znovu nejvýše 1x týdně.
os.environ.setdefault("CACHE_REFRESH_WINDOW_DAYS", "999999")
os.environ.setdefault("CACHE_NEAR_EXPIRY_DAYS", "-999999")
os.environ.setdefault("CACHE_DAILY_SECONDS", WEEK_SECONDS)
os.environ.setdefault("CACHE_NEAR_SECONDS", WEEK_SECONDS)
os.environ.setdefault("CACHE_EXPIRED_SECONDS", WEEK_SECONDS)

# eDalnice / service_fleet.py: stejný týdenní interval bez zrychlování před expirací.
os.environ.setdefault("VIGNETTE_CACHE_WINDOW_DAYS", "999999")
os.environ.setdefault("VIGNETTE_CACHE_NEAR_DAYS", "-999999")
os.environ.setdefault("VIGNETTE_CACHE_DAILY_SECONDS", WEEK_SECONDS)
os.environ.setdefault("VIGNETTE_CACHE_NEAR_SECONDS", WEEK_SECONDS)
os.environ.setdefault("VIGNETTE_CACHE_EXPIRED_SECONDS", WEEK_SECONDS)
