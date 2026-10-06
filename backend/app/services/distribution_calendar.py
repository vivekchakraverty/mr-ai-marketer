"""Offline, source-labelled observances and country/subdivision holiday calendars."""
from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache

import holidays

UN_SOURCE = "https://www.un.org/en/observances/list-days-weeks"
HOLIDAY_SOURCE = "https://holidays.readthedocs.io/en/latest/"

# Annual dates, not the sometimes stale year-specific dates printed on the UN list.
# Movable observances and multi-day periods are generated separately below.
_FIXED = """
01-04|World Braille Day
01-24|International Day of Education
01-26|International Day of Clean Energy
01-27|International Holocaust Remembrance Day
01-28|International Day of Peaceful Coexistence
02-02|World Wetlands Day
02-04|International Day of Human Fraternity
02-06|International Day of Zero Tolerance for Female Genital Mutilation
02-10|World Pulses Day
02-10|International Day of the Arabian Leopard
02-11|International Day of Women and Girls in Science
02-12|International Day for the Prevention of Violent Extremism
02-13|World Radio Day
02-17|Global Tourism Resilience Day
02-20|World Day of Social Justice
02-21|International Mother Language Day
03-01|Zero Discrimination Day
03-03|World Wildlife Day
03-04|World Engineering Day for Sustainable Development
03-05|International Day for Disarmament and Non-Proliferation Awareness
03-08|International Women's Day
03-10|International Day of Women Judges
03-15|International Day to Combat Islamophobia
03-20|International Day of Happiness
03-20|French Language Day
03-21|International Day for the Elimination of Racial Discrimination
03-21|World Poetry Day
03-21|International Day of Nowruz
03-21|World Down Syndrome Day
03-21|International Day of Forests
03-22|World Water Day
03-23|World Meteorological Day
03-24|World Tuberculosis Day
03-24|International Day for the Right to the Truth Concerning Gross Human Rights Violations
03-25|International Day of Remembrance of the Victims of Slavery
03-25|International Day of Solidarity with Detained and Missing Staff Members
03-30|International Day of Zero Waste
04-02|World Autism Awareness Day
04-04|International Day for Mine Awareness
04-05|International Day of Conscience
04-06|International Day of Sport for Development and Peace
04-07|World Health Day
04-07|International Day of Reflection on the 1994 Genocide against the Tutsi in Rwanda
04-12|International Day of Human Space Flight
04-14|World Chagas Disease Day
04-15|World Art Day
04-20|Chinese Language Day
04-21|World Creativity and Innovation Day
04-22|International Mother Earth Day
04-23|World Book and Copyright Day
04-23|English Language Day
04-23|Spanish Language Day
04-24|International Day of Multilateralism and Diplomacy for Peace
04-25|World Malaria Day
04-25|International Delegate's Day
04-26|World Intellectual Property Day
04-26|International Chernobyl Disaster Remembrance Day
04-28|World Day for Safety and Health at Work
04-30|International Jazz Day
05-02|World Tuna Day
05-03|World Press Freedom Day
05-05|World Portuguese Language Day
05-08|Time of Remembrance and Reconciliation for Those Who Lost Their Lives during the Second World War
05-09|Time of Remembrance and Reconciliation for Those Who Lost Their Lives during the Second World War
05-10|International Day of Argania
05-12|International Day of Plant Health
05-15|International Day of Families
05-16|International Day of Living Together in Peace
05-16|International Day of Light
05-17|World Telecommunication and Information Society Day
05-19|World Fair Play Day
05-20|World Bee Day
05-21|International Tea Day
05-21|World Day for Cultural Diversity for Dialogue and Development
05-22|International Day for Biological Diversity
05-23|International Day to End Obstetric Fistula
05-24|International Day of the Markhor
05-25|World Football Day
05-29|International Day of UN Peacekeepers
05-30|International Day of Potato
05-31|World No Tobacco Day
06-01|Global Day of Parents
06-03|World Bicycle Day
06-04|International Day of Innocent Children Victims of Aggression
06-05|World Environment Day
06-05|International Day for the Fight against Illegal, Unreported and Unregulated Fishing
06-06|Russian Language Day
06-07|World Food Safety Day
06-08|World Oceans Day
06-12|World Day Against Child Labour
06-13|International Albinism Awareness Day
06-14|World Blood Donor Day
06-15|World Elder Abuse Awareness Day
06-16|International Day of Family Remittances
06-17|World Day to Combat Desertification and Drought
06-18|Sustainable Gastronomy Day
06-18|International Day for Countering Hate Speech
06-19|International Day for the Elimination of Sexual Violence in Conflict
06-20|World Refugee Day
06-21|International Day of Yoga
06-21|International Day of the Celebration of the Solstice
06-23|United Nations Public Service Day
06-23|International Widows' Day
06-24|International Day of Women in Diplomacy
06-25|Day of the Seafarer
06-26|International Day against Drug Abuse and Illicit Trafficking
06-26|United Nations International Day in Support of Victims of Torture
06-27|Micro-, Small and Medium-sized Enterprises Day
06-29|International Day of the Tropics
06-30|International Asteroid Day
06-30|International Day of Parliamentarism
07-06|World Rural Development Day
07-07|World Kiswahili Language Day
07-11|World Population Day
07-11|World Horse Day
07-11|International Day of Reflection and Commemoration of the 1995 Genocide in Srebrenica
07-12|International Day of Combating Sand and Dust Storms
07-12|International Day of Hope
07-15|World Youth Skills Day
07-18|Nelson Mandela International Day
07-20|World Chess Day
07-20|International Moon Day
07-25|World Drowning Prevention Day
07-25|International Day of Women and Girls of African Descent
07-25|International Day for Judicial Well-being
07-28|World Hepatitis Day
07-30|International Day of Friendship
07-30|World Day against Trafficking in Persons
08-09|International Day of the World's Indigenous Peoples
08-11|World Steelpan Day
08-12|International Youth Day
08-19|World Humanitarian Day
08-21|International Day of Remembrance and Tribute to the Victims of Terrorism
08-22|International Day Commemorating the Victims of Acts of Violence Based on Religion or Belief
08-23|International Day for the Remembrance of the Slave Trade and Its Abolition
08-27|World Lake Day
08-29|International Day against Nuclear Tests
08-30|International Day of the Victims of Enforced Disappearances
08-31|International Day for People of African Descent
09-05|International Day of Charity
09-05|International Day of the World's Indigenous Women and Girls
09-07|International Day of Clean Air for Blue Skies
09-07|International Day of Police Cooperation
09-07|World Duchenne Awareness Day
09-08|International Literacy Day
09-09|International Day to Protect Education from Attack
09-12|United Nations Day for South-South Cooperation
09-15|International Day of Democracy
09-16|International Day for the Preservation of the Ozone Layer
09-16|International Day of Science, Technology and Innovation for the South
09-16|International Day for Interventional Cardiology
09-17|World Patient Safety Day
09-18|International Equal Pay Day
09-20|World Cleanup Day
09-21|International Day of Peace
09-23|International Day of Sign Languages
09-26|International Day for the Total Elimination of Nuclear Weapons
09-27|World Tourism Day
09-28|International Day for Universal Access to Information
09-29|International Day of Awareness of Food Loss and Waste
09-30|International Translation Day
10-01|International Coffee Day
10-01|International Day of Older Persons
10-02|International Day of Non-Violence
10-05|World Teachers' Day
10-07|World Cotton Day
10-09|World Post Day
10-10|World Mental Health Day
10-11|International Day of the Girl Child
10-13|International Day for Disaster Risk Reduction
10-15|International Day of Rural Women
10-16|World Food Day
10-17|International Day for the Eradication of Poverty
10-23|International Day of the Snow Leopard
10-24|United Nations Day
10-24|World Development Information Day
10-27|World Day for Audiovisual Heritage
10-29|International Day of Care and Support
10-31|World Cities Day
11-02|International Day to End Impunity for Crimes against Journalists
11-05|World Tsunami Awareness Day
11-06|International Day for Preventing the Exploitation of the Environment in War and Armed Conflict
11-10|World Science Day for Peace and Development
11-14|World Diabetes Day
11-15|International Day for the Prevention of and Fight against All Forms of Transnational Organized Crime
11-16|International Day for Tolerance
11-18|World Day for the Prevention of and Healing from Child Sexual Exploitation, Abuse and Violence
11-19|World Toilet Day
11-20|Africa Industrialization Day
11-20|World Children's Day
11-21|World Television Day
11-24|World Conjoined Twins Day
11-25|International Day for the Elimination of Violence against Women
11-26|World Sustainable Transport Day
11-29|International Day of Solidarity with the Palestinian People
11-30|Day of Remembrance for All Victims of Chemical Warfare
12-01|World AIDS Day
12-02|International Day for the Abolition of Slavery
12-03|International Day of Persons with Disabilities
12-04|International Day of Banks
12-04|International Day Against Unilateral Coercive Measures
12-05|International Volunteer Day
12-05|World Soil Day
12-07|International Civil Aviation Day
12-09|International Anti-Corruption Day
12-09|International Day of Commemoration and Dignity of the Victims of the Crime of Genocide
12-10|Human Rights Day
12-11|International Mountain Day
12-12|International Day of Neutrality
12-12|International Universal Health Coverage Day
12-18|International Migrants Day
12-18|Arabic Language Day
12-20|International Human Solidarity Day
12-27|International Day of Epidemic Preparedness
"""


def _weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (occurrence - 1))


def international_events(year: int) -> list[tuple[date, str]]:
    rows = [(date.fromisoformat(f"{year}-{day}"), name)
            for day, name in (line.split("|", 1) for line in _FIXED.strip().splitlines())]
    rows.extend([
        (_weekday(year, 7, 5, 1), "International Day of Cooperatives"),
        (_weekday(year, 10, 0, 1), "World Habitat Day"),
        (_weekday(year, 5, 5, 2), "World Migratory Bird Day"),
        (_weekday(year, 10, 5, 2), "World Migratory Bird Day"),
        (_weekday(year, 11, 6, 3), "World Day of Remembrance for Road Traffic Victims"),
        (_weekday(year, 11, 3, 3), "World Philosophy Day"),
        (_weekday(year, 4, 3, 4), "International Girls in ICT Day"),
    ])
    last_september = date(year, 9, 30)
    rows.append((last_september - timedelta(days=(last_september.weekday() - 3) % 7), "World Maritime Day"))
    for month, first, last, name in (
        (2, 1, 7, "World Interfaith Harmony Week"),
        (3, 21, 27, "Week of Solidarity with the Peoples Struggling against Racism and Racial Discrimination"),
        (5, 25, 31, "Week of Solidarity with the Peoples of Non-Self-Governing Territories"),
        (8, 1, 7, "World Breastfeeding Week"),
        (10, 4, 10, "World Space Week"),
        (10, 24, 30, "Disarmament Week"),
        (10, 24, 31, "Global Media and Information Literacy Week"),
        (11, 9, 15, "International Week of Science and Peace"),
        (11, 18, 24, "World Antimicrobial Resistance Awareness Week"),
    ):
        rows.extend((date(year, month, day), name) for day in range(first, last + 1))
    return rows


@lru_cache(maxsize=1)
def countries() -> tuple[str, ...]:
    return tuple(sorted(holidays.list_supported_countries(include_aliases=False)))


@lru_cache(maxsize=256)
def regions(country: str) -> tuple[tuple[str, str], ...]:
    if country not in countries():
        raise ValueError("Choose a supported country.")
    calendar = holidays.country_holidays(country, language="en_US")
    aliases = calendar.subdivisions_aliases
    return tuple((code, next((name for name, target in aliases.items()
                             if target == code and len(name) > len(code)), code))
                 for code in calendar.subdivisions)


@lru_cache(maxsize=512)
def _country_year(country: str, region: str, year: int) -> tuple[dict, ...]:
    names = dict(regions(country))
    prototype = holidays.country_holidays(country, language="en_US")
    # Include optional/unofficial/religious categories as well as public holidays.
    # Workday adjustments are not celebrations; bank/school-only closures add noise.
    categories = tuple(c for c in prototype.supported_categories
                       if c not in {"workday", "bank", "school", "half_day"})
    options = dict(years=year, language="en_US", categories=categories, expand=False)
    national = holidays.country_holidays(country, **options)
    collected: dict[tuple[date, str], dict] = {}
    for day in national:
        for name in national.get_list(day):
            collected[(day, name)] = {"date": day.isoformat(), "name": name,
                                      "scope": "national", "country": country, "regions": [],
                                      "source": HOLIDAY_SOURCE}
    subdivisions = names if region == "all" else {region: names[region]} if region else {}
    for code, label in subdivisions.items():
        local = holidays.country_holidays(country, subdiv=code, **options)
        for day in local:
            for name in local.get_list(day):
                key = (day, name)
                if key in collected and collected[key]["scope"] == "national":
                    continue
                row = collected.setdefault(key, {"date": day.isoformat(), "name": name,
                    "scope": "regional", "country": country, "regions": [], "source": HOLIDAY_SOURCE})
                row["regions"].append(label)
    # Celebration calendars also include festivals omitted from a closure list
    # because they fall on a Sunday. Keep this annual state celebration explicit.
    if country == "IN" and "KA" in subdivisions and year >= 1956:
        day = date(year, 11, 1)
        collected.setdefault((day, "Kannada Rajyotsava (Karnataka Formation Day)"), {
            "date": day.isoformat(), "name": "Kannada Rajyotsava (Karnataka Formation Day)",
            "scope": "regional", "country": "IN", "regions": [names["KA"]],
            "source": "https://bengaluruurban.nic.in/en/festival/kannada-rajyotsava/",
        })
    return tuple(collected.values())


def events(start: date, country: str = "", region: str = "all") -> dict:
    if country and country != "worldwide" and country not in countries():
        raise ValueError("Choose a supported country.")
    if country and country != "worldwide" and region not in {"", "all", *dict(regions(country))}:
        raise ValueError("Choose a state or region belonging to that country.")
    end = start + timedelta(days=29)
    years = range(start.year, end.year + 1)
    rows = [{"date": day.isoformat(), "name": name, "scope": "international",
             "country": None, "regions": [], "source": UN_SOURCE}
            for year in years for day, name in international_events(year)
            if start <= day <= end]
    selected = countries() if country == "worldwide" else (country,) if country else ()
    for code in selected:
        for year in years:
            rows.extend(row for row in _country_year(code, region if country != "worldwide" else "all", year)
                        if start.isoformat() <= row["date"] <= end.isoformat())
    rows.sort(key=lambda row: (row["date"], row["scope"], row["name"], row["country"] or ""))
    return {"start": start.isoformat(), "end": end.isoformat(), "events": rows,
            "dataset_version": holidays.__version__,
            "coverage": "UN observances and supported national, religious, cultural and regional holidays. Local festivals may be absent; dates marked estimated can change with local announcements."}
