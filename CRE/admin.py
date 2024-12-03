from django.contrib import admin

from .models import Country, RegionOrState, City, Branch, Restaurant

@admin.register(Country)
class CountryAdmin(admin.ModelAdmin):
    list_display = ('name', )

@admin.register(Restaurant)
class RestaurantAdmin(admin.ModelAdmin):
    list_display = ('name', )