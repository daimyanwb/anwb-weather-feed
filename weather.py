from ecmwf.opendata import Client
import eccodes
import math
import pandas as pd
from datetime import timedelta, datetime, timezone
from pathlib import Path


# =========================================================
# INSTELLINGEN
# =========================================================

CITY = "Amsterdam"
LAT = 52.37
LON = 4.90

GRIB_FILE = Path("amsterdam_weather.grib2")
CSV_FILE = Path("weather_feed_amsterdam.csv")

# Forecaststappen van 0 t/m 168 uur, per 6 uur
STEPS = list(range(0, 169, 6))

PARAMETERS = [
    "tp",      # Totale neerslag
    "2t",      # Temperatuur op 2 meter
    "ssrd",    # Zonnestraling aan het aardoppervlak
    "tcc",     # Totale bewolking
    "10u",     # Windcomponent west-oost
    "10v"      # Windcomponent zuid-noord
]

ADVERTENTIE_NAMES = [
    "geen-wind",
    "wel-wind",
    "geen-regen",
    "wel-regen"
]


# =========================================================
# ECMWF-DATA DOWNLOADEN
# =========================================================

print("Nieuwste ECMWF IFS-data ophalen...")

client = Client(
    source="azure",
    model="ifs",
    resol="0p25"
)

client.retrieve(
    time=0,
    type="fc",
    stream="oper",
    step=STEPS,
    param=PARAMETERS,
    target=str(GRIB_FILE)
)

print("ECMWF-data gedownload.")


# =========================================================
# DICHTSTBIJZIJNDE GRIDPUNT BIJ AMSTERDAM UITLEZEN
# =========================================================

weather_data = {}
model_run = None
used_grid_lat = None
used_grid_lon = None
distance_km = None

with GRIB_FILE.open("rb") as file:
    while True:
        gid = eccodes.codes_grib_new_from_file(file)

        if gid is None:
            break

        try:
            parameter = eccodes.codes_get(
                gid,
                "shortName"
            )

            if parameter not in PARAMETERS:
                continue

            step = int(
                eccodes.codes_get(
                    gid,
                    "endStep"
                )
            )

            nearest = eccodes.codes_grib_find_nearest(
                gid,
                LAT,
                LON
            )[0]

            value = float(nearest["value"])

            used_grid_lat = float(nearest["lat"])
            used_grid_lon = float(nearest["lon"])
            distance_km = float(nearest["distance"])

            date = str(
                eccodes.codes_get(
                    gid,
                    "dataDate"
                )
            )

            time = int(
                eccodes.codes_get(
                    gid,
                    "dataTime"
                )
            )

            run_time = pd.to_datetime(
                f"{date} {time:04d}",
                format="%Y%m%d %H%M",
                utc=True
            )

            if model_run is None:
                model_run = run_time

            valid_time = (
                run_time
                + timedelta(hours=step)
            )

            if valid_time not in weather_data:
                weather_data[valid_time] = {}

            weather_data[valid_time][parameter] = value

        finally:
            eccodes.codes_release(gid)


if not weather_data:
    raise RuntimeError(
        "Er zijn geen bruikbare ECMWF-gegevens gevonden."
    )


# =========================================================
# RUWE WAARDEN OMREKENEN
# =========================================================

rows = []

for valid_time, values in sorted(
    weather_data.items()
):
    temperature = values.get("2t")
    precipitation = values.get("tp")
    cloud_cover = values.get("tcc")
    solar_radiation = values.get("ssrd")
    wind_u = values.get("10u")
    wind_v = values.get("10v")

    # Kelvin naar graden Celsius
    if temperature is not None:
        temperature = temperature - 273.15

    # Meter water naar millimeter
    if precipitation is not None:
        precipitation = precipitation * 1000

    # Bewolking van 0-1 naar percentage
    if cloud_cover is not None:
        cloud_cover = cloud_cover * 100

    # Windcomponenten omrekenen naar windsnelheid
    wind_speed_ms = None

    if wind_u is not None and wind_v is not None:
        wind_speed_ms = math.sqrt(
            wind_u ** 2 + wind_v ** 2
        )

    rows.append({
        "tijd": valid_time,
        "temperatuur_c": temperature,
        "neerslag_cumulatief_mm": precipitation,
        "bewolking_pct": cloud_cover,
        "zonnestraling_j_m2": solar_radiation,
        "windsnelheid_m_s": wind_speed_ms
    })


df = pd.DataFrame(rows)
df = df.sort_values("tijd").reset_index(
    drop=True
)


# =========================================================
# SAMENVATTING KOMENDE 7 DAGEN
# =========================================================

temperature_values = (
    df["temperatuur_c"].dropna()
)

precipitation_values = (
    df["neerslag_cumulatief_mm"].dropna()
)

cloud_values = (
    df["bewolking_pct"].dropna()
)

solar_values = (
    df["zonnestraling_j_m2"].dropna()
)

wind_values = (
    df["windsnelheid_m_s"].dropna()
)


if precipitation_values.empty:
    raise RuntimeError(
        "Neerslagdata ontbreekt."
    )

if temperature_values.empty:
    raise RuntimeError(
        "Temperatuurdata ontbreekt."
    )


# ECMWF total precipitation is cumulatief
# vanaf de start van de modelrun
precipitation_7d_mm = max(
    0,
    precipitation_values.iloc[-1]
    - precipitation_values.iloc[0]
)

temperature_avg_c = temperature_values.mean()
temperature_min_c = temperature_values.min()
temperature_max_c = temperature_values.max()

cloud_cover_avg_pct = (
    cloud_values.mean()
    if not cloud_values.empty
    else None
)

wind_avg_kmh = (
    wind_values.mean() * 3.6
    if not wind_values.empty
    else None
)

wind_max_kmh = (
    wind_values.max() * 3.6
    if not wind_values.empty
    else None
)


# Zonnestraling omrekenen van joule
# naar kWh per vierkante meter
solar_radiation_7d_kwh_m2 = None

if not solar_values.empty:
    solar_radiation_7d_kwh_m2 = max(
        0,
        (
            solar_values.iloc[-1]
            - solar_values.iloc[0]
        ) / 3_600_000
    )


# =========================================================
# WEATHER TRIGGER BEPALEN
# =========================================================

# Eerste marketingdrempels voor de pilot.
# Deze kunnen later worden aangepast.

if precipitation_7d_mm >= 10:
    weather_trigger = "rain"

elif (
    temperature_max_c >= 20
    and (
        cloud_cover_avg_pct is None
        or cloud_cover_avg_pct <= 60
    )
):
    weather_trigger = "warm_sunny"

elif temperature_avg_c <= 8:
    weather_trigger = "cold"

elif (
    wind_avg_kmh is not None
    and wind_avg_kmh >= 30
):
    weather_trigger = "windy"

else:
    weather_trigger = "neutral"


# =========================================================
# DYNAMISCHE ADVERTENTIETEKST
# =========================================================

precipitation_visual_mm = round(
    precipitation_7d_mm
)

if weather_trigger == "rain":
    visual_headline = (
        f"Regen verwacht in {CITY}"
    )

    visual_weather_text = (
        "Meer dan 10 mm in de komende 7 dagen"
    )

    product_category = "regenproducten"

elif weather_trigger == "warm_sunny":
    visual_headline = (
        f"Zonnige dagen verwacht in {CITY}"
    )

    visual_weather_text = (
        "Tijd om naar buiten te gaan"
    )

    product_category = "zomerproducten"

elif weather_trigger == "cold":
    visual_headline = (
        f"Koudere dagen verwacht in {CITY}"
    )

    visual_weather_text = (
        "Bereid je voor op de kou"
    )

    product_category = "warme kleding"

elif weather_trigger == "windy":
    visual_headline = (
        f"Winderige dagen verwacht in {CITY}"
    )

    visual_weather_text = (
        "Bereid je voor op onstuimig weer"
    )

    product_category = "outdoorproducten"

else:
    visual_headline = (
        f"Ga voorbereid op pad in {CITY}"
    )

    visual_weather_text = (
        "Bekijk het actuele assortiment"
    )

    product_category = "algemeen"


# =========================================================
# BASISREGEL VOOR DE FEED MAKEN
# =========================================================

generated_at = datetime.now(timezone.utc)

base_feed_row = {
    "city": CITY,

    "latitude_requested": LAT,
    "longitude_requested": LON,

    "latitude_gridpoint": round(
        used_grid_lat,
        2
    ),

    "longitude_gridpoint": round(
        used_grid_lon,
        2
    ),

    "gridpoint_distance_km": round(
        distance_km,
        1
    ),

    "forecast_start": (
        df["tijd"].min().isoformat()
    ),

    "forecast_end": (
        df["tijd"].max().isoformat()
    ),

    "forecast_period": "komende 7 dagen",

    "precipitation_7d_mm": round(
        precipitation_7d_mm,
        1
    ),

    "precipitation_visual_mm": (
        precipitation_visual_mm
    ),

    "temperature_avg_c": round(
        temperature_avg_c,
        1
    ),

    "temperature_min_c": round(
        temperature_min_c,
        1
    ),

    "temperature_max_c": round(
        temperature_max_c,
        1
    ),

    "solar_radiation_7d_kwh_m2": (
        round(
            solar_radiation_7d_kwh_m2,
            1
        )
        if solar_radiation_7d_kwh_m2 is not None
        else ""
    ),

    "cloud_cover_avg_pct": (
        round(cloud_cover_avg_pct)
        if cloud_cover_avg_pct is not None
        else ""
    ),

    "wind_avg_kmh": (
        round(wind_avg_kmh, 1)
        if wind_avg_kmh is not None
        else ""
    ),

    "wind_max_kmh": (
        round(wind_max_kmh, 1)
        if wind_max_kmh is not None
        else ""
    ),

    "weather_trigger": weather_trigger,
    "product_category": product_category,

    "visual_headline": visual_headline,
    "visual_weather_text": visual_weather_text,

    "model": "ECMWF IFS",
    "model_run_utc": model_run.isoformat(),
    "generated_at_utc": generated_at.isoformat(),

    "source": "ECMWF Open Data",
    "source_license": "CC BY 4.0"
}


# =========================================================
# VIER ADVERTENTIE-ITEMS MAKEN
# =========================================================

feed_rows = []

for advertentie_name in ADVERTENTIE_NAMES:
    row = base_feed_row.copy()

    row["id"] = (
        f"{CITY.lower()}-{advertentie_name}"
    )

    row["advertentie_name"] = advertentie_name

    feed_rows.append(row)


feed = pd.DataFrame(feed_rows)


# Volgorde van de belangrijkste kolommen bepalen
first_columns = [
    "id",
    "advertentie_name",
    "city"
]

remaining_columns = [
    column
    for column in feed.columns
    if column not in first_columns
]

feed = feed[
    first_columns + remaining_columns
]


# =========================================================
# CSV OPSLAAN
# =========================================================

feed.to_csv(
    CSV_FILE,
    index=False,
    encoding="utf-8"
)


# =========================================================
# CONTROLE-UITKOMST
# =========================================================

print()
print("Weather feed succesvol aangemaakt.")
print(f"Bestand: {CSV_FILE}")
print(f"Locatie: {CITY}")

print(
    f"ECMWF-gridpunt: "
    f"{used_grid_lat}, {used_grid_lon}"
)

print(
    f"Afstand tot Amsterdam: "
    f"{distance_km:.1f} km"
)

print(
    f"Neerslag komende 7 dagen: "
    f"{precipitation_7d_mm:.1f} mm"
)

print(
    f"Gemiddelde temperatuur: "
    f"{temperature_avg_c:.1f} graden Celsius"
)

print(
    f"Gemiddelde wind: "
    f"{wind_avg_kmh:.1f} km/u"
)

print(
    f"Weertrigger: {weather_trigger}"
)

print()
print("Aangemaakte advertentie-items:")

for advertentie_name in ADVERTENTIE_NAMES:
    print(
        f"- {CITY.lower()}-{advertentie_name}"
    )

print()
print(f"Visual: {visual_headline}")
print(f"Subtekst: {visual_weather_text}")
