from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import render_to_response, get_object_or_404
from django.template import Context, loader, RequestContext
from hits.models import Hit


def hits_list_context(template, more_map={}):
    unfinished_hit_list = Hit.objects.filter(completed=False).order_by('id')
    finished_hit_list = Hit.objects.filter(completed=True).order_by('-id')
    c = Context(
        dict(
            {
                'unfinished_hit_list': unfinished_hit_list,
                'finished_hit_list': finished_hit_list
            },
            **more_map
        )
    )
    return template.render(c)


def index(request):
    t = loader.get_template('hits/index.html')
    return HttpResponse(hits_list_context(t))


def detail(request, hit_id):
    h = get_object_or_404(Hit, pk=hit_id)
    num_left = len(Hit.objects.filter(completed=False).order_by('id'))
    return render_to_response(
        'hits/detail.html',
        {'hit': h, 'num_left': num_left},
        context_instance=RequestContext(request)
    )


def results(request, hit_id):
    return HttpResponse("You're looking at the results of hit %s." % hit_id)


def submission(request, hit_id):

    unfinished_hit_list = Hit.objects.filter(completed=False).order_by('id')
    unfinished_hit_ids = [str(x.id) for x in unfinished_hit_list]

    h = get_object_or_404(Hit, pk=hit_id)
    h.completed = True
    h.answers = dict(request.POST.items())
    h.save()

    # forward to next hit if this was an unfinished hit
    hit_id = str(hit_id)
    if hit_id in unfinished_hit_ids and len(unfinished_hit_ids) > 1:
        unfinished_hit_ids = [x for x in unfinished_hit_ids if x != hit_id]
        next_hit = unfinished_hit_ids[0]
        return HttpResponseRedirect('/hits/' + next_hit)

    t = loader.get_template('hits/submission.html')
    return HttpResponse(hits_list_context(t, {'submitted_hit': h}))
