# Generated by Django 5.0.4 on 2025-07-04 17:04

import django.contrib.postgres.fields
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('CRE', '0012_remove_shiftpattern_at_least_one_target_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='shiftpattern',
            name='roles',
            field=django.contrib.postgres.fields.ArrayField(base_field=models.CharField(choices=[('company_admin', 'Company Admin'), ('restaurant_owner', 'Restaurant Owner'), ('country_manager', 'Country Manager'), ('restaurant_manager', 'Restaurant Manager'), ('branch_manager', 'Branch Manager'), ('shift_leader', 'Shift Leader'), ('cashier', 'Cashier'), ('cook', 'Cook'), ('food_runner', 'Food Runner'), ('cleaner', 'Cleaner'), ('delivery_man', 'Delivery Man'), ('utility_worker', 'Utility Worker')], max_length=30), blank=True, default=list, help_text='List of roles this pattern applies to', null=True, size=10),
        ),
        migrations.AlterField(
            model_name='shiftpattern',
            name='users',
            field=django.contrib.postgres.fields.ArrayField(base_field=models.IntegerField(), blank=True, default=list, help_text='List of user IDs this pattern applies to', null=True, size=None),
        ),
    ]
