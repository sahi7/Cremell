# Generated by Django 5.0.4 on 2025-04-03 19:25

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('notifications', '0004_alter_branchactivity_activity_type_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='branchactivity',
            name='activity_type',
            field=models.CharField(choices=[('order_create', 'Order Created'), ('order_modify', 'Order Modified'), ('task_claim', 'Task Claimed'), ('task_complete', 'Task Completed'), ('payment_process', 'Payment Processed'), ('staff_available', 'Staff Availability Changed'), ('escalation', 'Task Escalated'), ('field_update', 'Update Field'), ('staff_hire', 'Staff Hired'), ('staff_terminate', 'Staff Terminated'), ('shift_change', 'Shift Schedule Changed'), ('payment_processed', 'Payment Processed'), ('refund_issued', 'Refund Issued'), ('discount_applied', 'Discount Applied'), ('expense_logged', 'Expense Recorded'), ('reservation', 'Reservation Made'), ('complaint', 'Customer Complaint'), ('allergy_alert', 'Allergy Alert Recorded'), ('menu_update', 'Menu Updated'), ('item_add', 'Menu Item Added'), ('item_remove', 'Menu Item Removed'), ('price_change', 'Price Changed')], max_length=20),
        ),
    ]
