# Generated by Django 5.0.4 on 2025-07-04 16:20

import django.contrib.postgres.fields
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('CRE', '0011_alter_staffshift_branch_and_more'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='shiftpattern',
            name='at_least_one_target',
        ),
        migrations.RemoveIndex(
            model_name='shiftpattern',
            name='CRE_shiftpa_role_2f9e02_idx',
        ),
        migrations.RemoveField(
            model_name='shiftpattern',
            name='role',
        ),
        migrations.RemoveField(
            model_name='shiftpattern',
            name='user',
        ),
        migrations.AddField(
            model_name='shiftpattern',
            name='roles',
            field=django.contrib.postgres.fields.ArrayField(base_field=models.CharField(choices=[('company_admin', 'Company Admin'), ('restaurant_owner', 'Restaurant Owner'), ('country_manager', 'Country Manager'), ('restaurant_manager', 'Restaurant Manager'), ('branch_manager', 'Branch Manager'), ('shift_leader', 'Shift Leader'), ('cashier', 'Cashier'), ('cook', 'Cook'), ('food_runner', 'Food Runner'), ('cleaner', 'Cleaner'), ('delivery_man', 'Delivery Man'), ('utility_worker', 'Utility Worker')], max_length=30), default=list, help_text='List of roles this pattern applies to', size=10),
        ),
        migrations.AddField(
            model_name='shiftpattern',
            name='users',
            field=django.contrib.postgres.fields.ArrayField(base_field=models.IntegerField(), blank=True, default=list, help_text='List of user IDs this pattern applies to', size=None),
        ),
        migrations.AddIndex(
            model_name='shiftpattern',
            index=models.Index(fields=['roles'], name='roles_idx'),
        ),
        migrations.AddIndex(
            model_name='shiftpattern',
            index=models.Index(fields=['users'], name='users_idx'),
        ),
        migrations.AddConstraint(
            model_name='shiftpattern',
            constraint=models.CheckConstraint(check=models.Q(('roles__len__gt', 0), ('users__len__gt', 0), _connector='OR'), name='at_least_one_target'),
        ),
    ]
