try:
    from cStringIO import StringIO
except ImportError:
    try:
        from StringIO import StringIO
    except ImportError:
        from io import BytesIO
        StringIO = BytesIO

# hack to add unicode() to python3 for backward compatibility
try:
    unicode('')
except NameError:
    unicode = str

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.db.utils import OperationalError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from functools import wraps

from turkle.models import Hit, HitAssignment, Batch, Project


def handle_db_lock(func):
    """Decorator that catches database lock errors from sqlite"""
    @wraps(func)
    def wrapper(request, *args, **kwargs):
        try:
            return func(request, *args, **kwargs)
        except OperationalError as ex:
            # sqlite3 cannot handle concurrent transactions.
            # This should be very rare with just a few users.
            # If it happens often, switch to mysql or postgres.
            if str(ex) == 'database is locked':
                messages.error(request, u'The database is busy. Please try again.')
                return redirect(index)
            raise ex
    return wrapper


@handle_db_lock
def accept_hit(request, batch_id, hit_id):
    """
    Security behavior:
    - If the user does not have permission to access the Batch+Task, they
      are redirected to the index page with an error message.
    """
    try:
        batch = Batch.objects.get(id=batch_id)
    except ObjectDoesNotExist:
        messages.error(request, u'Cannot find Task Batch with ID {}'.format(batch_id))
        return redirect(index)
    try:
        hit = Hit.objects.get(id=hit_id)
    except ObjectDoesNotExist:
        messages.error(request, u'Cannot find Task with ID {}'.format(hit_id))
        return redirect(index)

    try:
        with transaction.atomic():
            # Lock access to the specified Hit
            Hit.objects.filter(id=hit_id).select_for_update()

            # Will throw ObjectDoesNotExist exception if Hit no longer available
            batch.available_hits_for(request.user).get(id=hit_id)

            ha = HitAssignment()
            if request.user.is_authenticated:
                ha.assigned_to = request.user
            else:
                ha.assigned_to = None
            ha.hit = hit
            ha.save()
    except ObjectDoesNotExist:
        messages.error(request, u'The Task with ID {} is no longer available'.format(hit_id))
        return redirect(index)

    return redirect(hit_assignment, hit.id, ha.id)


@handle_db_lock
def accept_next_hit(request, batch_id):
    """
    Security behavior:
    - If the user does not have permission to access the Batch+Task, they
      are redirected to the index page with an error message.
    """
    try:
        with transaction.atomic():
            batch = Batch.objects.get(id=batch_id)

            # Lock access to all Tasks available to current user in the batch
            batch.available_hit_ids_for(request.user).select_for_update()

            hit_id = _skip_aware_next_available_hit_id(request, batch)

            if hit_id:
                ha = HitAssignment()
                if request.user.is_authenticated:
                    ha.assigned_to = request.user
                else:
                    ha.assigned_to = None
                ha.hit_id = hit_id
                ha.save()
    except ObjectDoesNotExist:
        messages.error(request, u'Cannot find Task Batch with ID {}'.format(batch_id))
        return redirect(index)

    if hit_id:
        return redirect(hit_assignment, hit_id, ha.id)
    else:
        messages.error(request, u'No more Tasks available from Batch {}'.format(batch_id))
        return redirect(index)


@staff_member_required
def download_batch_csv(request, batch_id):
    """
    Security behavior:
    - Access to this page is limited to requesters.  Any requester can
      download any CSV file.
    """
    batch = Batch.objects.get(id=batch_id)
    csv_output = StringIO()
    batch.to_csv(csv_output)
    csv_string = csv_output.getvalue()
    response = HttpResponse(csv_string, content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="{}"'.format(
        batch.csv_results_filename())
    return response


@staff_member_required
def expire_abandoned_assignments(request):
    """
    Security behavior:
    - Access to this page is limited to requesters.
    """
    (total_deleted, _) = HitAssignment.expire_all_abandoned()
    messages.info(request, u'All {} abandoned Tasks have been expired'.format(total_deleted))
    return redirect('/admin/turkle')


def hit_assignment(request, hit_id, hit_assignment_id):
    """
    Security behavior:
    - If the user does not have permission to access the Task Assignment, they
      are redirected to the index page with an error message.
    """
    try:
        hit = Hit.objects.get(id=hit_id)
    except ObjectDoesNotExist:
        messages.error(request, u'Cannot find Task with ID {}'.format(hit_id))
        return redirect(index)
    try:
        hit_assignment = HitAssignment.objects.get(id=hit_assignment_id)
    except ObjectDoesNotExist:
        messages.error(request,
                       u'Cannot find Task Assignment with ID {}'.format(hit_assignment_id))
        return redirect(index)

    if request.user.is_authenticated:
        if request.user != hit_assignment.assigned_to:
            messages.error(
                request,
                u'You do not have permission to work on the Task Assignment with ID {}'.
                format(hit_assignment.id))
            return redirect(index)
    else:
        if hit_assignment.assigned_to is not None:
            messages.error(
                request,
                u'You do not have permission to work on the Task Assignment with ID {}'.
                format(hit_assignment.id))
            return redirect(index)

    auto_accept_status = request.session.get('auto_accept_status', False)

    if request.method == 'GET':
        return render(
            request,
            'hit_assignment.html',
            {
                'auto_accept_status': auto_accept_status,
                'hit': hit,
                'hit_assignment': hit_assignment,
            },
        )
    else:
        hit_assignment.answers = dict(request.POST.items())
        hit_assignment.completed = True
        hit_assignment.save()

        if request.session.get('auto_accept_status'):
            return redirect(accept_next_hit, hit.batch.id)
        else:
            return redirect(index)


def hit_assignment_iframe(request, hit_id, hit_assignment_id):
    """
    Security behavior:
    - If the user does not have permission to access the Task Assignment, they
      are redirected to the index page with an error messge.
    """
    try:
        hit = Hit.objects.get(id=hit_id)
    except ObjectDoesNotExist:
        messages.error(request, u'Cannot find Task with ID {}'.format(hit_id))
        return redirect(index)
    try:
        hit_assignment = HitAssignment.objects.get(id=hit_assignment_id)
    except ObjectDoesNotExist:
        messages.error(request,
                       u'Cannot find Task Assignment with ID {}'.format(hit_assignment_id))
        return redirect(index)

    if request.user.is_authenticated:
        if request.user != hit_assignment.assigned_to:
            messages.error(
                request,
                u'You do not have permission to work on the Task Assignment with ID {}'.
                format(hit_assignment.id))
            return redirect(index)

    return render(
        request,
        'hit_assignment_iframe.html',
        {
            'hit': hit,
            'hit_assignment': hit_assignment,
        },
    )


def index(request):
    """
    Security behavior:
    - Anyone can access the page, but the page only shows the user
      information they have access to.
    """
    abandoned_assignments = []
    if request.user.is_authenticated:
        for ha in HitAssignment.objects.filter(assigned_to=request.user).filter(completed=False):
            abandoned_assignments.append({
                'hit': ha.hit,
                'hit_assignment_id': ha.id
            })

    # Create a row for each Batch that has Tasks available for the current user
    batch_rows = []
    for project in Project.available_for(request.user):
        for batch in project.batches_available_for(request.user):
            total_hits_available = batch.total_available_hits_for(request.user)
            if total_hits_available > 0:
                batch_rows.append({
                    'project_name': project.name,
                    'batch_name': batch.name,
                    'batch_published': batch.date_published,
                    'assignments_available': total_hits_available,
                    'preview_next_hit_url': reverse('preview_next_hit',
                                                    kwargs={'batch_id': batch.id}),
                    'accept_next_hit_url': reverse('accept_next_hit',
                                                   kwargs={'batch_id': batch.id})
                })
    return render(request, 'index.html', {
        'abandoned_assignments': abandoned_assignments,
        'batch_rows': batch_rows
    })


def preview(request, hit_id):
    """
    Security behavior:
    - If the user does not have permission to access the Task, they
      are redirected to the index page with an error message.
    """
    try:
        hit = Hit.objects.get(id=hit_id)
    except ObjectDoesNotExist:
        messages.error(request, u'Cannot find Task with ID {}'.format(hit_id))
        return redirect(index)

    if not request.user.is_authenticated and hit.batch.project.login_required:
        messages.error(request, u'You do not have permission to view this Task')
        return redirect(index)

    return render(request, 'preview.html', {'hit': hit})


def preview_iframe(request, hit_id):
    """
    Security behavior:
    - If the user does not have permission to access the Task, they
      are redirected to the index page with an error message.
    """
    try:
        hit = Hit.objects.get(id=hit_id)
    except ObjectDoesNotExist:
        messages.error(request, u'Cannot find Task with ID {}'.format(hit_id))
        return redirect(index)

    if not request.user.is_authenticated and hit.batch.project.login_required:
        messages.error(request, u'You do not have permission to view this Task')
        return redirect(index)

    return render(request, 'preview_iframe.html', {'hit': hit})


def preview_next_hit(request, batch_id):
    """
    Security behavior:
    - If the user does not have permission to access the Batch, they
      are redirected to the index page with an error message.
    """
    try:
        batch = Batch.objects.get(id=batch_id)
    except ObjectDoesNotExist:
        messages.error(request, u'Cannot find Task Batch with ID {}'.format(batch_id))
        return redirect(index)

    hit_id = _skip_aware_next_available_hit_id(request, batch)

    if hit_id:
        return redirect(preview, hit_id)
    else:
        messages.error(request,
                       u'No more Tasks are available for Batch "{}"'.format(batch.name))
        return redirect(index)


def return_hit_assignment(request, hit_id, hit_assignment_id):
    """
    Security behavior:
    - If the user does not have permission to return the Assignment, they
      are redirected to the index page with an error message.
    """
    redirect_due_to_error = _delete_hit_assignment(request, hit_id, hit_assignment_id)
    if redirect_due_to_error:
        return redirect_due_to_error
    return redirect(index)


def skip_and_accept_next_hit(request, batch_id, hit_id, hit_assignment_id):
    """
    Security behavior:
    - If the user does not have permission to return the Assignment, they
      are redirected to the index page with an error message.
    """
    redirect_due_to_error = _delete_hit_assignment(request, hit_id, hit_assignment_id)
    if redirect_due_to_error:
        return redirect_due_to_error

    _add_hit_id_to_skip_session(request.session, batch_id, hit_id)
    return redirect(accept_next_hit, batch_id)


def skip_hit(request, batch_id, hit_id):
    """
    Security behavior:
    - This view updates a session variable that controls the order
      that Tasks are presented to a user.  Users cannot modify other
      users session variables.
    """
    _add_hit_id_to_skip_session(request.session, batch_id, hit_id)
    return redirect(preview_next_hit, batch_id)


def update_auto_accept(request):
    """
    Security behavior:
    - This view updates a session variable that controls whether or
      not Task Assignments are auto-accepted.  Users cannot modify other
      users session variables.
    """
    accept_status = (request.POST[u'auto_accept'] == u'true')
    request.session['auto_accept_status'] = accept_status
    return JsonResponse({})


def _add_hit_id_to_skip_session(session, batch_id, hit_id):
    """Add Hit ID to session variable tracking Tasks the user has skipped
    """
    # The Django session store converts dictionary keys from ints to strings
    batch_id = unicode(batch_id)
    hit_id = unicode(hit_id)

    if 'skipped_hits_in_batch' not in session:
        session['skipped_hits_in_batch'] = {}
    if batch_id not in session['skipped_hits_in_batch']:
        session['skipped_hits_in_batch'][batch_id] = []
        session.modified = True
    if hit_id not in session['skipped_hits_in_batch'][batch_id]:
        session['skipped_hits_in_batch'][batch_id].append(hit_id)
        session.modified = True


@handle_db_lock
def _delete_hit_assignment(request, hit_id, hit_assignment_id):
    """Delete a HitAssignment, if possible

    Returns:
        - None if the HitAssignment can be deleted, *OR*
        - An HTTPResponse object created by redirect() if there was an error

    Usage:
        redirect_due_to_error = _delete_hit_assignment(request, hit_id, hit_assignment_id)
        if redirect_due_to_error:
            return redirect_due_to_error
    """
    try:
        hit = Hit.objects.get(id=hit_id)
    except ObjectDoesNotExist:
        messages.error(request, u'Cannot find Task with ID {}'.format(hit_id))
        return redirect(index)
    try:
        hit_assignment = HitAssignment.objects.get(id=hit_assignment_id)
    except ObjectDoesNotExist:
        messages.error(request,
                       u'Cannot find Task Assignment with ID {}'.format(hit_assignment_id))
        return redirect(index)

    if hit_assignment.completed:
        messages.error(request, u"The Task can't be returned because it has been completed")
        return redirect(index)
    if request.user.is_authenticated:
        if hit_assignment.assigned_to != request.user:
            messages.error(request, u'The Task you are trying to return belongs to another user')
            return redirect(index)
    else:
        if hit_assignment.assigned_to is not None:
            messages.error(request, u'The Task you are trying to return belongs to another user')
            return redirect(index)
        if hit.batch.project.login_required:
            messages.error(request, u'You do not have permission to access this Task')
            return redirect(index)

    with transaction.atomic():
        # Lock access to the specified Hit
        Hit.objects.filter(id=hit_id).select_for_update()

        hit_assignment.delete()


def _skip_aware_next_available_hit_id(request, batch):
    """Get next available Task for user, taking into account previously skipped Tasks

    This function will first look for an available Task that the user
    has not previously skipped.  If the only available Tasks are Tasks
    that the user has skipped, this function will return the first
    such Task.

    Returns:
        Hit ID (int), or None if no more Tasks are available
    """
    def _get_skipped_hit_ids_for_batch(session, batch_id):
        batch_id = unicode(batch_id)
        if 'skipped_hits_in_batch' in session and \
           batch_id in session['skipped_hits_in_batch']:
            return session['skipped_hits_in_batch'][batch_id]
        else:
            return None

    available_hit_ids = batch.available_hit_ids_for(request.user)
    skipped_ids = _get_skipped_hit_ids_for_batch(request.session, batch.id)

    if skipped_ids:
        hit_id = available_hit_ids.exclude(id__in=skipped_ids).first()
        if not hit_id:
            hit_id = available_hit_ids.filter(id__in=skipped_ids).first()
            if hit_id:
                messages.info(request, u'Only previously skipped Tasks are available')

                # Once all remaining Tasks have been marked as skipped, we clear
                # their skipped status.  If we don't take this step, then a Task
                # cannot be skipped a second time.
                request.session['skipped_hits_in_batch'][unicode(batch.id)] = []
                request.session.modified = True
    else:
        hit_id = available_hit_ids.first()

    return hit_id