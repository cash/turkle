from django.conf.urls import patterns, url

urlpatterns = patterns('scalpl.views',
    url(r'^(?P<doc_id>\w+)/$', 'view'),
)