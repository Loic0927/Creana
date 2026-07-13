from django.db import migrations, models


def ensure_push_ai_finished_column(apps, schema_editor):
    """Align fresh databases and production databases with a legacy column."""
    UserSettings = apps.get_model("socialmanager", "UserSettings")
    table = UserSettings._meta.db_table

    with schema_editor.connection.cursor() as cursor:
        columns = {
            column.name
            for column in schema_editor.connection.introspection.get_table_description(
                cursor,
                table,
            )
        }

    if "push_ai_finished" not in columns:
        field = models.BooleanField(default=False)
        field.set_attributes_from_name("push_ai_finished")
        field.model = UserSettings
        schema_editor.add_field(UserSettings, field)
        return

    quoted_table = schema_editor.quote_name(table)
    quoted_column = schema_editor.quote_name("push_ai_finished")
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            f"UPDATE {quoted_table} SET {quoted_column} = %s "
            f"WHERE {quoted_column} IS NULL",
            [False],
        )
        if schema_editor.connection.vendor == "postgresql":
            cursor.execute(
                f"ALTER TABLE {quoted_table} ALTER COLUMN {quoted_column} "
                "SET DEFAULT FALSE"
            )
            cursor.execute(
                f"ALTER TABLE {quoted_table} ALTER COLUMN {quoted_column} "
                "SET NOT NULL"
            )


class Migration(migrations.Migration):
    dependencies = [
        ("socialmanager", "0036_web_push_notifications"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(
                    ensure_push_ai_finished_column,
                    migrations.RunPython.noop,
                ),
            ],
            state_operations=[
                migrations.AddField(
                    model_name="usersettings",
                    name="push_ai_finished",
                    field=models.BooleanField(default=False),
                ),
            ],
        ),
    ]
