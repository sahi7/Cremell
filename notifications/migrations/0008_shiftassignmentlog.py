# Generated by Django 5.0.4 on 2025-05-18 05:48

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('CRE', '0008_shiftpattern_shiftpattern_at_least_one_target'),
        ('notifications', '0007_alter_employeetransfer_to_branch_roleassignment'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ShiftAssignmentLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('date', models.DateField()),
                ('assigned_at', models.DateTimeField(auto_now_add=True)),
                ('branch', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='CRE.branch')),
                ('pattern', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to='CRE.shiftpattern')),
                ('shift', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='CRE.shift')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ],
        ),
    ]
