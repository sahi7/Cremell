# Generated by Django 5.0.4 on 2025-05-18 05:48

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('CRE', '0007_country_language_alter_country_timezone'),
    ]

    operations = [
        migrations.CreateModel(
            name='ShiftPattern',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('role', models.CharField(choices=[('company_admin', 'Company Admin'), ('restaurant_owner', 'Restaurant Owner'), ('country_manager', 'Country Manager'), ('restaurant_manager', 'Restaurant Manager'), ('branch_manager', 'Branch Manager'), ('shift_leader', 'Shift Leader'), ('cashier', 'Cashier'), ('cook', 'Cook'), ('food_runner', 'Food Runner'), ('cleaner', 'Cleaner'), ('delivery_man', 'Delivery Man'), ('utility_worker', 'Utility Worker')], max_length=30)),
                ('pattern_type', models.CharField(choices=[('RB', 'Role-Based'), ('US', 'User-Specific'), ('RT', 'Rotating'), ('AH', 'Ad-Hoc'), ('HY', 'Hybrid')], max_length=2)),
                ('config', models.JSONField()),
                ('priority', models.IntegerField(default=1)),
                ('active_from', models.DateField()),
                ('active_until', models.DateField(blank=True, null=True)),
                ('is_temp', models.BooleanField(default=False)),
                ('branch', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='CRE.branch')),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'indexes': [models.Index(fields=['branch', 'active_from', 'active_until'], name='CRE_shiftpa_branch__ae2974_idx'), models.Index(fields=['role', 'user'], name='CRE_shiftpa_role_2f9e02_idx')],
            },
        ),
        migrations.AddConstraint(
            model_name='shiftpattern',
            constraint=models.CheckConstraint(check=models.Q(('role__isnull', False), ('user__isnull', False), _connector='OR'), name='at_least_one_target'),
        ),
    ]
