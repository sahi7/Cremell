import os
import requests
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Carousel.settings")

import django
django.setup()
from CRE.models import Country, RegionOrState, City
from django.core.exceptions import ObjectDoesNotExist
from django.db.utils import IntegrityError

# Example function to populate countries
def populate_countries():
    url = "http://api.geonames.org/countryInfoJSON?username=archilesm" # https://download.geonames.org/countryInfoJSON
    response = requests.get(url)
    
    # Ensure the response is valid before processing
    if response.status_code != 200:
        print("Error fetching data from GeoNames API")
        return
    
    countries = response.json().get('geonames', [])
    
    # Loop through countries and insert if not already present
    for country in countries:
        # Check if the country already exists based on either 'code' or 'name'
        if not Country.objects.filter(code=country['isoAlpha3']).exists() and \
           not Country.objects.filter(name=country['countryName']).exists():
            Country.objects.create(
                code=country['isoAlpha3'],
                name=country['countryName'],
                currency=country.get('currencyCode', ''),  # Default to empty if no currencyCode
                language=country.get('languages', ''),    # Default to empty if no languages
                continent=country.get('continentName', '')  # Default to empty if no continentName
            )
        else:
            print(f"Skipping {country['countryName']} as it already exists.")

# Example function to populate regions/states (for a specific country)
def populate_regions(geonameId):
    try:
        url = f"http://api.geonames.org/childrenJSON?geonameId={geonameId}&username=archilesm"
        response = requests.get(url)
        
        if response.status_code == 200:
            regions = response.json().get('geonames', [])
            if regions:
                for region in regions:
                    country_name = region['countryName']
                    
                    # Retrieve the Country instance based on the countryName
                    try:
                        country = Country.objects.get(name=country_name)
                    except Country.DoesNotExist:
                        print(f"Country '{country_name}' not found in the database.")
                        continue
                    
                    # Create the RegionOrState entry with the correct country
                    RegionOrState.objects.create(
                        name=region['name'],
                        country=country
                    )
                print(f"Populated {len(regions)} regions for geonameId {geonameId}.")
            else:
                print(f"No regions found for geonameId {geonameId}.")
        else:
            print(f"Failed to fetch regions. Status Code: {response.status_code}")
    
    except requests.exceptions.RequestException as e:
        print(f"Error: {e}")

# Example function to populate cities (for a specific state/region)
import requests
from django.core.exceptions import ObjectDoesNotExist

def populate_cities(region_name, country_id):
    # Step 1: Fetch the RegionOrState object from the database
    try:
        region = RegionOrState.objects.get(name=region_name)
    except ObjectDoesNotExist:
        print(f"Region or State '{region_name}' not found in the database.")
        return

    # Step 2: Query the GeoNames API with the region_name
    url = f"http://api.geonames.org/searchJSON?q={region_name}&maxRows=1000&username=archilesm"
    response = requests.get(url)
    
    # Check if the request was successful
    if response.status_code != 200:
        print(f"Failed to fetch data from GeoNames API. Status code: {response.status_code}")
        return

    # Step 3: Parse the JSON response
    data = response.json()
    cities = data.get('geonames', [])

    # Step 4: Iterate over the cities and populate the City table
    for city in cities:
        # Check if the adminName1 matches the region_name and countryId matches
        if city.get('adminName1') == region_name and city.get('countryId') == country_id:
            try:
                # Use get_or_create to avoid duplicates
                City.objects.get_or_create(
                    name=city['name'],
                    region_or_state=region,
                    defaults={'name': city['name'], 'region_or_state': region}
                )
            except IntegrityError:
                # Skip if a duplicate entry is found
                print(f"City '{city['name']}' already exists in region '{region_name}'. Skipping.")
                continue

    print(f"Successfully populated cities for region '{region_name}'.")

countryId = "2233387"
# Call functions to populate data
# populate_countries()
# populate_regions("2233387")  # Example for CMR
populate_cities("Littoral", countryId) # Example for California
