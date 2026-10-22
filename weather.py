from ecmwf.opendata import Client
import eccodes
import math
import pandas as pd

from datetime import timedelta, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


# =========================================================
# INSTELLINGEN
# =========================================================

CITY = "Amsterdam"
LAT = 52.37
LON = 4.90

WEATHER_GRIB_FILE = Path(
    "amsterdam_weather.grib2"
)

ENSEMBLE_GRIB_FILE = Path(
    "amsterdam_rain_ensemble.grib2"
)

CSV_FILE = Path(
    "weather_feed_amsterdam.csv"
)

# Normale forecast: 7 dagen, per 6 uur
FORECAST_STEPS = list(
    range(0, 169, 6)
)

# Ensembleforecast:
# voldoende voor morgen plus de twee dagen daarna
ENSEMBLE_STEPS = list(
    range(0, 97, 6)
)

WEATHER_PARAMETERS = [
    "tp",      # Totale neerslag
    "2t",      # Temperatuur op 2 meter
    "ssrd",    # Zonnestraling
    "tcc",     # Totale bewolking
    "10u",     # Windcomponent west-oost
    "10v"      # Windcomponent zuid-noord
]

FEED_ITEM_NAMES = [
    "geen-weersdata",
    "weersdata-morgen",
    "weersdata-komende-3-dagen"
]

# Minimaal 0,1 mm binnen een periode van 6 uur
# telt als "regen verwacht".
RAIN_THRESHOLD_MM = 0.1

LOCAL_TIMEZONE = ZoneInfo(
    "Europe/Amsterdam"
)


# =========================================================
# EVENTUELE OUDE GRIB-BESTANDEN VERWIJDEREN
# =========================================================

for temporary_file in [
    WEATHER_GRIB_FILE,
    ENSEMBLE_GRIB_FILE
]:
    if temporary_file.exists():
        temporary_file.unlink()


# =========================================================
# ECMWF CLIENT
# =========================================================

client = Client(
    source="azure",
    model="ifs",
    resol="0p25"
)


# =========================================================
# NORMALE ECMWF-FORECAST DOWNLOADEN
# =========================================================

print(
    "Nieuwste ECMWF IFS-forecast ophalen..."
)

client.retrieve(
    time=0,
    type="fc",
    stream="oper",
    step=FORECAST_STEPS,
    param=WEATHER_PARAMETERS,
    target=str(WEATHER_GRIB_FILE)
)

print(
    "Normale ECMWF-forecast gedownload."
)


# =========================================================
# ECMWF ENSEMBLEFORECAST DOWNLOADEN
# =========================================================

print(
    "ECMWF ensembleforecast voor regenkans ophalen..."
)

client.retrieve(
    time=0,
    type="pf",
    stream="enfo",
    levtype="sfc",
    step=ENSEMBLE_STEPS,
    param=["tp"],
    target=str(ENSEMBLE_GRIB_FILE)
)

print(
    "ECMWF ensembleforecast gedownload."
)


# =========================================================
# NORMALE FORECAST UITLEZEN
# =========================================================

weather_data = {}

model_run = None
used_grid_lat = None
used_grid_lon = None
distance_km = None

with WEATHER_GRIB_FILE.open("rb") as file:

    while True:

        gid = eccodes.codes_grib_new_from_file(
            file
        )

        if gid is None:
            break

        try:

            parameter = eccodes.codes_get(
                gid,
                "shortName"
            )

            if parameter not in WEATHER_PARAMETERS:
                continue

            step = int(
                eccodes.codes_get(
                    gid,
                    "endStep"
                )
            )

            nearest = (
                eccodes.codes_grib_find_nearest(
                    gid,
                    LAT,
                    LON
                )[0]
            )

            value = float(
                nearest["value"]
            )

            used_grid_lat = float(
                nearest["lat"]
            )

            used_grid_lon = float(
                nearest["lon"]
            )

            distance_km = float(
                nearest["distance"]
            )

            data_date = str(
                eccodes.codes_get(
                    gid,
                    "dataDate"
                )
            )

            data_time = int(
                eccodes.codes_get(
                    gid,
                    "dataTime"
                )
            )

            run_time = pd.to_datetime(
                f"{data_date} {data_time:04d}",
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

            weather_data[
                valid_time
            ][parameter] = value

        finally:

            eccodes.codes_release(gid)


if not weather_data:
    raise RuntimeError(
        "Er zijn geen normale ECMWF-gegevens gevonden."
    )


# =========================================================
# NORMALE WEERWAARDEN OMREKENEN
# =========================================================

weather_rows = []

for valid_time, values in sorted(
    weather_data.items()
):

    temperature = values.get("2t")
    precipitation = values.get("tp")
    cloud_cover = values.get("tcc")
    solar_radiation = values.get("ssrd")
    wind_u = values.get("10u")
    wind_v = values.get("10v")

    # Kelvin naar Celsius
    if temperature is not None:
        temperature = (
            temperature - 273.15
        )

    # Meter water naar millimeter
    if precipitation is not None:
        precipitation = (
            precipitation * 1000
        )

    # Bewolking van 0-1 naar percentage
    if cloud_cover is not None:
        cloud_cover = (
            cloud_cover * 100
        )

    wind_speed_ms = None

    if (
        wind_u is not None
        and wind_v is not None
    ):
        wind_speed_ms = math.sqrt(
            wind_u ** 2
            + wind_v ** 2
        )

    weather_rows.append({
        "tijd_utc": valid_time,
        "temperatuur_c": temperature,
        "neerslag_cumulatief_mm": precipitation,
        "bewolking_pct": cloud_cover,
        "zonnestraling_j_m2": solar_radiation,
        "windsnelheid_m_s": wind_speed_ms
    })


weather_df = pd.DataFrame(
    weather_rows
)

weather_df = (
    weather_df
    .sort_values("tijd_utc")
    .reset_index(drop=True)
)


# =========================================================
# ENSEMBLEFORECAST UITLEZEN
# =========================================================

ensemble_data = {}

ensemble_model_run = None

with ENSEMBLE_GRIB_FILE.open("rb") as file:

    while True:

        gid = eccodes.codes_grib_new_from_file(
            file
        )

        if gid is None:
            break

        try:

            parameter = eccodes.codes_get(
                gid,
                "shortName"
            )

            if parameter != "tp":
                continue

            step = int(
                eccodes.codes_get(
                    gid,
                    "endStep"
                )
            )

            member = int(
                eccodes.codes_get(
                    gid,
                    "number"
                )
            )

            nearest = (
                eccodes.codes_grib_find_nearest(
                    gid,
                    LAT,
                    LON
                )[0]
            )

            # Neerslag van meter naar millimeter
            cumulative_precipitation_mm = (
                float(nearest["value"])
                * 1000
            )

            data_date = str(
                eccodes.codes_get(
                    gid,
                    "dataDate"
                )
            )

            data_time = int(
                eccodes.codes_get(
                    gid,
                    "dataTime"
                )
            )

            run_time = pd.to_datetime(
                f"{data_date} {data_time:04d}",
                format="%Y%m%d %H%M",
                utc=True
            )

            if ensemble_model_run is None:
                ensemble_model_run = run_time

            valid_time = (
                run_time
                + timedelta(hours=step)
            )

            ensemble_data[
                (member, valid_time)
            ] = cumulative_precipitation_mm

        finally:

            eccodes.codes_release(gid)


if not ensemble_data:
    raise RuntimeError(
        "Er zijn geen ECMWF-ensemblegegevens gevonden."
    )


# =========================================================
# NEERSLAG PER 6 UUR PER ENSEMBLELID BEREKENEN
# =========================================================

ensemble_rows = []

ensemble_members = sorted({
    member
    for member, valid_time
    in ensemble_data.keys()
})

for member in ensemble_members:

    member_forecast = sorted(
        [
            (valid_time, value)
            for (
                current_member,
                valid_time
            ), value in ensemble_data.items()
            if current_member == member
        ],
        key=lambda item: item[0]
    )

    previous_cumulative_mm = None

    for valid_time, cumulative_mm in member_forecast:

        if previous_cumulative_mm is None:

            interval_precipitation_mm = max(
                0,
                cumulative_mm
            )

        else:

            interval_precipitation_mm = max(
                0,
                cumulative_mm
                - previous_cumulative_mm
            )

        previous_cumulative_mm = (
            cumulative_mm
        )

        ensemble_rows.append({
            "ensemble_member": member,
            "tijd_utc": valid_time,
            "neerslag_6u_mm": (
                interval_precipitation_mm
            ),
            "regen_verwacht": (
                interval_precipitation_mm
                >= RAIN_THRESHOLD_MM
            )
        })


ensemble_df = pd.DataFrame(
    ensemble_rows
)

if ensemble_df.empty:
    raise RuntimeError(
        "De ensembledata kon niet worden verwerkt."
    )


# =========================================================
# KANS OP REGEN PER FORECASTMOMENT
# =========================================================

rain_probability_df = (
    ensemble_df
    .groupby(
        "tijd_utc",
        as_index=False
    )
    .agg(
        kans_op_regen_pct=(
            "regen_verwacht",
            lambda values:
            values.mean() * 100
        ),
        aantal_ensembleleden=(
            "ensemble_member",
            "nunique"
        )
    )
)


# =========================================================
# UTC OMZETTEN NAAR LOKALE TIJD
# =========================================================

rain_probability_df[
    "tijd_lokaal"
] = (
    rain_probability_df[
        "tijd_utc"
    ]
    .dt
    .tz_convert(
        LOCAL_TIMEZONE
    )
)

rain_probability_df[
    "datum_lokaal"
] = (
    rain_probability_df[
        "tijd_lokaal"
    ]
    .dt
    .date
)


# =========================================================
# DATUMPERIODES BEPALEN
# =========================================================

today_local = datetime.now(
    LOCAL_TIMEZONE
).date()

tomorrow_local = (
    today_local
    + timedelta(days=1)
)

third_day_local = (
    today_local
    + timedelta(days=3)
)


# =========================================================
# GEMIDDELDE KANS OP REGEN MORGEN
# =========================================================

tomorrow_probabilities = (
    rain_probability_df.loc[
        (
            rain_probability_df[
                "datum_lokaal"
            ]
            == tomorrow_local
        ),
        "kans_op_regen_pct"
    ]
)

if tomorrow_probabilities.empty:
    raise RuntimeError(
        "Er zijn geen regenkansen voor morgen gevonden."
    )

rain_chance_tomorrow_pct = round(
    tomorrow_probabilities.mean()
)


# =========================================================
# GEMIDDELDE KANS OP REGEN KOMENDE 3 DAGEN
# =========================================================

next_3_days_probabilities = (
    rain_probability_df.loc[
        (
            rain_probability_df[
                "datum_lokaal"
            ]
            >= tomorrow_local
        )
        &
        (
            rain_probability_df[
                "datum_lokaal"
            ]
            <= third_day_local
        ),
        "kans_op_regen_pct"
    ]
)

if next_3_days_probabilities.empty:
    raise RuntimeError(
        "Er zijn geen regenkansen voor de "
        "komende drie dagen gevonden."
    )

rain_chance_next_3_days_pct = round(
    next_3_days_probabilities.mean()
)

ensemble_member_count = int(
    rain_probability_df[
        "aantal_ensembleleden"
    ].max()
)


# =========================================================
# SAMENVATTING NORMALE FORECAST
# =========================================================

temperature_values = (
    weather_df[
        "temperatuur_c"
    ]
    .dropna()
)

precipitation_values = (
    weather_df[
        "neerslag_cumulatief_mm"
    ]
    .dropna()
)

cloud_values = (
    weather_df[
        "bewolking_pct"
    ]
    .dropna()
)

solar_values = (
    weather_df[
        "zonnestraling_j_m2"
    ]
    .dropna()
)

wind_values = (
    weather_df[
        "windsnelheid_m_s"
    ]
    .dropna()
)


if temperature_values.empty:
    raise RuntimeError(
        "Temperatuurdata ontbreekt."
    )

if precipitation_values.empty:
    raise RuntimeError(
        "Neerslagdata ontbreekt."
    )


# Totale neerslag over de volledige periode
precipitation_7d_mm = max(
    0,
    precipitation_values.iloc[-1]
    - precipitation_values.iloc[0]
)

temperature_avg_c = (
    temperature_values.mean()
)

temperature_min_c = (
    temperature_values.min()
)

temperature_max_c = (
    temperature_values.max()
)

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

solar_radiation_7d_kwh_m2 = None

if not solar_values.empty:

    solar_radiation_7d_kwh_m2 = max(
        0,
        (
            solar_values.iloc[-1]
            - solar_values.iloc[0]
        )
        / 3_600_000
    )


# =========================================================
# ALGEMENE WEATHER TRIGGER
# =========================================================

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
# BASISINFORMATIE VOOR ALLE FEEDITEMS
# =========================================================

generated_at = datetime.now(
    timezone.utc
)

common_feed_data = {

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
        weather_df[
            "tijd_utc"
        ]
        .min()
        .isoformat()
    ),

    "forecast_end": (
        weather_df[
            "tijd_utc"
        ]
        .max()
        .isoformat()
    ),

    "rain_probability_threshold_mm_6h": (
        RAIN_THRESHOLD_MM
    ),

    "ensemble_member_count": (
        ensemble_member_count
    ),

    "precipitation_7d_mm": round(
        precipitation_7d_mm,
        1
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

    "cloud_cover_avg_pct": (
        round(
            cloud_cover_avg_pct
        )
        if cloud_cover_avg_pct
        is not None
        else ""
    ),

    "solar_radiation_7d_kwh_m2": (
        round(
            solar_radiation_7d_kwh_m2,
            1
        )
        if solar_radiation_7d_kwh_m2
        is not None
        else ""
    ),

    "wind_avg_kmh": (
        round(
            wind_avg_kmh,
            1
        )
        if wind_avg_kmh
        is not None
        else ""
    ),

    "wind_max_kmh": (
        round(
            wind_max_kmh,
            1
        )
        if wind_max_kmh
        is not None
        else ""
    ),

    "weather_trigger": (
        weather_trigger
    ),

    "model": "ECMWF IFS",

    "model_run_utc": (
        model_run.isoformat()
    ),

    "ensemble_model_run_utc": (
        ensemble_model_run.isoformat()
    ),

    "generated_at_utc": (
        generated_at.isoformat()
    ),

    "source": "ECMWF Open Data",
    "source_license": "CC BY 4.0"
}


# =========================================================
# DRIE FEEDITEMS MAKEN
# =========================================================

feed_rows = []


# ---------------------------------------------------------
# ITEM 1: GEEN WEERSDATA
# ---------------------------------------------------------

no_weather_row = common_feed_data.copy()

no_weather_row.update({

    "id": (
        f"{CITY.lower()}-geen-weersdata"
    ),

    "advertentie_name": (
        "geen-weersdata"
    ),

    "weather_data_active": "false",

    "weather_period": "geen",

    "rain_chance_pct": "",

    "rain_chance_text": "",

    "visual_headline": (
        "Ga goed voorbereid op pad"
    ),

    "visual_weather_text": "",

    "product_category": "algemeen"
})

feed_rows.append(
    no_weather_row
)


# ---------------------------------------------------------
# ITEM 2: WEERSDATA MORGEN
# ---------------------------------------------------------

tomorrow_row = common_feed_data.copy()

tomorrow_row.update({

    "id": (
        f"{CITY.lower()}-weersdata-morgen"
    ),

    "advertentie_name": (
        "weersdata-morgen"
    ),

    "weather_data_active": "true",

    "weather_period": "morgen",

    "rain_chance_pct": (
        rain_chance_tomorrow_pct
    ),

    "rain_chance_text": (
        f"Morgen gemiddeld "
        f"{rain_chance_tomorrow_pct}% "
        f"kans op regen"
    ),

    "visual_headline": (
        f"Morgen "
        f"{rain_chance_tomorrow_pct}% "
        f"kans op regen"
    ),

    "visual_weather_text": (
        "Bereid je voor op regen"
    ),

    "product_category": (
        "regenproducten"
    )
})

feed_rows.append(
    tomorrow_row
)


# ---------------------------------------------------------
# ITEM 3: WEERSDATA KOMENDE 3 DAGEN
# ---------------------------------------------------------

next_3_days_row = common_feed_data.copy()

next_3_days_row.update({

    "id": (
        f"{CITY.lower()}-"
        f"weersdata-komende-3-dagen"
    ),

    "advertentie_name": (
        "weersdata-komende-3-dagen"
    ),

    "weather_data_active": "true",

    "weather_period": (
        "komende 3 dagen"
    ),

    "rain_chance_pct": (
        rain_chance_next_3_days_pct
    ),

    "rain_chance_text": (
        f"Komende 3 dagen gemiddeld "
        f"{rain_chance_next_3_days_pct}% "
        f"kans op regen"
    ),

    "visual_headline": (
        f"Komende 3 dagen gemiddeld "
        f"{rain_chance_next_3_days_pct}% "
        f"kans op regen"
    ),

    "visual_weather_text": (
        "Bereid je voor op regen"
    ),

    "product_category": (
        "regenproducten"
    )
})

feed_rows.append(
    next_3_days_row
)


# =========================================================
# DATAFRAME MAKEN
# =========================================================

feed = pd.DataFrame(
    feed_rows
)

first_columns = [
    "id",
    "advertentie_name",
    "city",
    "weather_data_active",
    "weather_period",
    "rain_chance_pct",
    "rain_chance_text"
]

remaining_columns = [
    column
    for column in feed.columns
    if column not in first_columns
]

feed = feed[
    first_columns
    + remaining_columns
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
# TIJDELIJKE BESTANDEN VERWIJDEREN
# =========================================================

for temporary_file in [
    WEATHER_GRIB_FILE,
    ENSEMBLE_GRIB_FILE
]:
    if temporary_file.exists():
        temporary_file.unlink()


# =========================================================
# CONTROLE-UITKOMST
# =========================================================

print()
print(
    "Weather feed succesvol aangemaakt."
)

print(
    f"Bestand: {CSV_FILE}"
)

print(
    f"Locatie: {CITY}"
)

print(
    f"ECMWF-gridpunt: "
    f"{used_grid_lat}, "
    f"{used_grid_lon}"
)

print(
    f"Afstand tot Amsterdam: "
    f"{distance_km:.1f} km"
)

print(
    f"Gebruikte ensembleleden: "
    f"{ensemble_member_count}"
)

print(
    f"Gemiddelde kans op regen morgen: "
    f"{rain_chance_tomorrow_pct}%"
)

print(
    f"Gemiddelde kans op regen "
    f"komende 3 dagen: "
    f"{rain_chance_next_3_days_pct}%"
)

print(
    f"Neerslag komende 7 dagen: "
    f"{precipitation_7d_mm:.1f} mm"
)

print(
    f"Gemiddelde temperatuur: "
    f"{temperature_avg_c:.1f} °C"
)

if wind_avg_kmh is not None:

    print(
        f"Gemiddelde wind: "
        f"{wind_avg_kmh:.1f} km/u"
    )

print()
print(
    "Aangemaakte feeditems:"
)

for item_name in FEED_ITEM_NAMES:

    print(
        f"- {CITY.lower()}-{item_name}"
    )

print()
print(
    "Feed gereed voor Channable."
)
