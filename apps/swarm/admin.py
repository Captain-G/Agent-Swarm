from django.contrib import admin

from . import models

for _name in dir(models):
    _obj = getattr(models, _name)
    if isinstance(_obj, type) and issubclass(_obj, models.models.Model) and not _obj._meta.abstract and _obj.__module__ == models.__name__:
        try:
            admin.site.register(_obj)
        except admin.sites.AlreadyRegistered:
            pass
