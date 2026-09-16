"""Optional link organization and password metadata; no changes to existing URL rows."""


def upgrade(database, models):
    database.create_tables([models.LinkMetadata], safe=True)
