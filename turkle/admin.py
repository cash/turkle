try:
    from cStringIO import StringIO
except ImportError:
    try:
        from StringIO import StringIO
    except ImportError:
        from io import BytesIO
        StringIO = BytesIO
import json
# hack to add unicode() to python3 for backward compatibility
try:
    unicode('')
except NameError:
    unicode = str

from django.conf.urls import url
from django.contrib import admin, messages
from django.contrib.admin.widgets import FilteredSelectMultiple
from django.contrib.auth.admin import GroupAdmin, UserAdmin
from django.contrib.auth.models import Group, User
from django.core.exceptions import ObjectDoesNotExist
from django.db import models
from django.forms import (FileField, FileInput, HiddenInput, IntegerField,
                          ModelForm, ModelMultipleChoiceField, TextInput, ValidationError, Widget)
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.html import format_html, format_html_join
from guardian.admin import GuardedModelAdmin
from guardian.shortcuts import assign_perm, get_groups_with_perms, remove_perm
import unicodecsv

from turkle.models import Batch, Project
from turkle.utils import get_site_name


class TurkleAdminSite(admin.AdminSite):
    app_index_template = 'admin/turkle/app_index.html'
    site_header = get_site_name() + ' administration'


class CustomGroupAdminForm(ModelForm):
    """Hides 'Permissions' section, adds 'Group Members' section
    """
    users = ModelMultipleChoiceField(
        label='Group Members',
        queryset=User.objects.all(),
        required=False,
        widget=FilteredSelectMultiple('User', False),
    )

    def __init__(self, *args, **kwargs):
        super(CustomGroupAdminForm, self).__init__(*args, **kwargs)
        if self.instance.id:
            self.fields['users'].initial = self.instance.user_set.all()


class CustomGroupAdmin(GroupAdmin):
    """Hides 'Permissions' section, adds 'Group Members' section
    """
    # We have to use custom logic to add a Users widget to GroupAdmin
    # that is not required when adding a Groups widget to UserAdmin.
    #
    # There is a ManyToMany relationship between Users and Groups, but
    # Django does not treat the two halves of a ManyToManyField
    # identically.  The relationship between the two models is
    # declared in just one of the models, e.g.:
    #
    #   class User(models.Model):
    #       groups = models.ManyToManyField(Group, related_name='user_set')
    #
    # This creates a "forward" relationship from User to Group, and a
    # "reverse" relationship from Group to User.
    #
    # ModelForm behaves very differently for the forward vs. reverse
    # halves of a ManyToManyField relationship.  The default Django
    # UserAdmin just lists 'groups' as one of the fields, and
    # ModelForm renders a FilterSelect widget showing all available
    # Groups.  But if we try to use 'user_set' as one of the fields
    # for GroupAdmin, Django raises a FieldError exception with the
    # message "Unknown field(s) (user_set)".  From Django's
    # perspective, 'groups' is a field of User, but 'user_set' is not
    # a field of Group.

    fields = ('name', 'users')
    form = CustomGroupAdminForm
    list_display = ('name', 'total_members')

    def save_model(self, request, obj, form, change):
        super(CustomGroupAdmin, self).save_model(request, obj, form, change)
        if u'users' in form.data:
            existing_users = set(obj.user_set.all())
            form_users = set(form.cleaned_data[u'users'])
            users_to_add = form_users.difference(existing_users)
            users_to_remove = existing_users.difference(form_users)
            for user in users_to_add:
                obj.user_set.add(user)
            for user in users_to_remove:
                obj.user_set.remove(user)
        else:
            obj.user_set.clear()

    def total_members(self, obj):
        return obj.user_set.count()


class CustomUserAdmin(UserAdmin):
    add_fieldsets = UserAdmin.add_fieldsets + (
        ('Personal info', {'fields': ('first_name', 'last_name', 'email')}),
        ('Permissions', {'fields': ('is_active', 'is_staff', 'is_superuser',
                                    'groups')}),
    )

    def response_add(self, request, obj, post_url_continue=None):
        return redirect(reverse('turkle_admin:auth_user_changelist'))


class CustomButtonFileWidget(FileInput):
    # HTML file inputs have a button followed by text that either
    # gives the filename or says "no file selected".  It is not
    # possible to modify that text using JavaScript.
    #
    # This template Hides the file input, creates a "Choose File"
    # button (linked to the hidden file input) followed by a span for
    # displaying custom text.
    template_name = "admin/forms/widgets/custom_button_file_widget.html"


class ProjectNameReadOnlyWidget(Widget):
    """Widget displays a link to Project.  Hidden form variable stores Project ID.
    """
    input_type = None

    def __init__(self, project, attrs=None):
        self.project_id = project.id
        self.project_name = project.name
        super(ProjectNameReadOnlyWidget, self).__init__(attrs)

    def render(self, name, value, attrs=None):
        return format_html(
            '<div class="readonly"><a href="{}">{}</a></div>'
            '<input name="project" id="id_project" type="hidden" value="{}" />'.format(
                reverse('admin:turkle_project_change', args=[self.project_id]),
                self.project_name, self.project_id))


class BatchForm(ModelForm):
    csv_file = FileField(label='CSV File')

    # Allow a form to be submitted without an 'allotted_assignment_time'
    # field.  The default value for this field will be used instead.
    # See also the function clean_allotted_assignment_time().
    allotted_assignment_time = IntegerField(
        initial=Batch._meta.get_field('allotted_assignment_time').get_default(),
        required=False)

    def __init__(self, *args, **kwargs):
        super(BatchForm, self).__init__(*args, **kwargs)

        self.fields['allotted_assignment_time'].label = 'Allotted assignment time (hours)'
        self.fields['allotted_assignment_time'].help_text = 'If a user abandons a HIT, ' + \
            'this determines how long it takes until their assignment is deleted and ' + \
            'someone else can work on the HIT.'
        self.fields['csv_file'].help_text = 'You can Drag-and-Drop a CSV file onto this ' + \
            'window, or use the "Choose File" button to browse for the file'
        self.fields['csv_file'].widget = CustomButtonFileWidget()
        self.fields['project'].label = 'Project'
        self.fields['name'].label = 'Batch Name'

        # csv_file field not required if changing existing Batch
        #
        # When changing a Batch, the BatchAdmin.get_fields()
        # function will cause the form to be rendered without
        # displaying an HTML form field for the csv_file field.  I was
        # running into strange behavior where Django would still try
        # to validate the csv_file form field, even though there was
        # no way for the user to provide a value for this field.  The
        # two lines below prevent this problem from occurring, by
        # making the csv_file field optional when changing
        # a Batch.
        if not self.instance._state.adding:
            self.fields['csv_file'].required = False
            self.fields['project'].widget = \
                ProjectNameReadOnlyWidget(self.instance.project)

    def clean(self):
        """Verify format of CSV file

        Verify that:
        - fieldnames in CSV file are identical to fieldnames in Project
        - number of fields in each row matches number of fields in CSV header
        """
        cleaned_data = super(BatchForm, self).clean()

        csv_file = cleaned_data.get("csv_file", False)
        project = cleaned_data.get("project")

        if not csv_file or not project:
            return

        validation_errors = []

        rows = unicodecsv.reader(csv_file)
        header = next(rows)

        csv_fields = set(header)
        template_fields = set(project.fieldnames)
        if csv_fields != template_fields:
            csv_but_not_template = csv_fields.difference(template_fields)
            if csv_but_not_template:
                validation_errors.append(
                    ValidationError(
                        'The CSV file contained fields that are not in the HTML template. '
                        'These extra fields are: %s' %
                        ', '.join(csv_but_not_template)))
            template_but_not_csv = template_fields.difference(csv_fields)
            if template_but_not_csv:
                validation_errors.append(
                    ValidationError(
                        'The CSV file is missing fields that are in the HTML template. '
                        'These missing fields are: %s' %
                        ', '.join(template_but_not_csv)))

        expected_fields = len(header)
        for (i, row) in enumerate(rows):
            if len(row) != expected_fields:
                validation_errors.append(
                    ValidationError(
                        'The CSV file header has %d fields, but line %d has %d fields' %
                        (expected_fields, i+2, len(row))))

        if validation_errors:
            raise ValidationError(validation_errors)

        # Rewind file, so it can be re-read
        csv_file.seek(0)

    def clean_allotted_assignment_time(self):
        """Clean 'allotted_assignment_time' form field

        - If the allotted_assignment_time field is not submitted as part
          of the form data (e.g. when interacting with this form via a
          script), use the default value.
        - If the allotted_assignment_time is an empty string (e.g. when
          submitting the form using a browser), raise a ValidationError
        """
        data = self.data.get('allotted_assignment_time')
        if data is None:
            return Batch._meta.get_field('allotted_assignment_time').get_default()
        elif data.strip() is u'':
            raise ValidationError('This field is required.')
        else:
            return data


class BatchAdmin(admin.ModelAdmin):
    form = BatchForm
    formfield_overrides = {
        models.CharField: {'widget': TextInput(attrs={'size': '60'})},
    }
    list_display = (
        'name', 'filename', 'total_hits', 'assignments_per_hit',
        'total_finished_hits', 'active', 'download_csv')

    def cancel_batch(self, request, batch_id):
        try:
            batch = Batch.objects.get(id=batch_id)
            batch.delete()
        except ObjectDoesNotExist:
            messages.error(request, u'Cannot find Batch with ID {}'.format(batch_id))

        return redirect(reverse('turkle_admin:turkle_batch_changelist'))

    def download_csv(self, obj):
        download_url = reverse('download_batch_csv', kwargs={'batch_id': obj.id})
        return format_html('<a href="{}">Download CSV results file</a>'.format(download_url))

    def get_fields(self, request, obj):
        # Display different fields when adding (when obj is None) vs changing a Batch
        if not obj:
            return ('project', 'name', 'assignments_per_hit',
                    'allotted_assignment_time', 'csv_file')
        else:
            return ('active', 'project', 'name', 'assignments_per_hit',
                    'allotted_assignment_time', 'filename')

    def get_readonly_fields(self, request, obj):
        if not obj:
            return []
        else:
            return ('assignments_per_hit', 'filename')

    def get_urls(self):
        urls = super(BatchAdmin, self).get_urls()
        my_urls = [
            url(r'^(?P<batch_id>\d+)/cancel/$',
                self.admin_site.admin_view(self.cancel_batch), name='cancel_batch'),
            url(r'^(?P<batch_id>\d+)/review/$',
                self.admin_site.admin_view(self.review_batch), name='review_batch'),
            url(r'^(?P<batch_id>\d+)/publish/$',
                self.admin_site.admin_view(self.publish_batch), name='publish_batch'),
        ]
        return my_urls + urls

    def publish_batch(self, request, batch_id):
        try:
            batch = Batch.objects.get(id=batch_id)
            batch.active = True
            batch.save()
        except ObjectDoesNotExist:
            messages.error(request, u'Cannot find Batch with ID {}'.format(batch_id))

        return redirect(reverse('turkle_admin:turkle_batch_changelist'))

    def response_add(self, request, obj, post_url_continue=None):
        return redirect(reverse('turkle_admin:review_batch', kwargs={'batch_id': obj.id}))

    def review_batch(self, request, batch_id):
        request.current_app = self.admin_site.name
        try:
            batch = Batch.objects.get(id=batch_id)
        except ObjectDoesNotExist:
            messages.error(request, u'Cannot find Batch with ID {}'.format(batch_id))
            return redirect(reverse('turkle_admin:turkle_batch_changelist'))

        hit_ids = list(batch.hit_set.values_list('id', flat=True))
        hit_ids_as_json = json.dumps(hit_ids)
        return render(request, 'admin/turkle/review_batch.html', {
            'batch_id': batch_id,
            'first_hit_id': hit_ids[0],
            'hit_ids_as_json': hit_ids_as_json,
            'site_header': self.admin_site.site_header,
            'site_title': self.admin_site.site_title,
        })

    def save_model(self, request, obj, form, change):
        if obj._state.adding:
            # If Batch active flag not explicitly set, make inactive until Batch reviewed
            if u'active' not in request.POST:
                obj.active = False

            # Only use CSV file when adding Batch, not when changing
            obj.filename = request.FILES['csv_file']._name
            csv_text = request.FILES['csv_file'].read()
            super(BatchAdmin, self).save_model(request, obj, form, change)
            csv_fh = StringIO(csv_text)
            obj.create_hits_from_csv(csv_fh)
        else:
            super(BatchAdmin, self).save_model(request, obj, form, change)


class ProjectForm(ModelForm):
    template_file_upload = FileField(label='HTML template file', required=False)
    worker_permissions = ModelMultipleChoiceField(
        label='Worker Groups with access to this Project',
        queryset=Group.objects.all(),
        required=False,
        widget=FilteredSelectMultiple('Worker Groups', False),
    )

    def __init__(self, *args, **kwargs):
        super(ProjectForm, self).__init__(*args, **kwargs)

        self.fields['template_file_upload'].widget = CustomButtonFileWidget()

        # This hidden form field is updated by JavaScript code in the
        # customized admin template file:
        #   turkle/templates/admin/turkle/project/change_form.html
        self.fields['filename'].widget = HiddenInput()

        self.fields['assignments_per_hit'].label = 'Assignments per Task'
        self.fields['assignments_per_hit'].help_text = 'This parameter sets the default ' + \
            'number of Assignments per Task for new Batches of Tasks.  Changing this ' + \
            'parameter DOES NOT change the number of Assignments per Task for already ' + \
            'published batches of Tasks.'
        self.fields['custom_permissions'].label = 'Restrict access to specific Groups of Workers '
        self.fields['html_template'].label = 'HTML template text'
        self.fields['html_template'].help_text = 'You can edit the template text directly, ' + \
            'Drag-and-Drop a template file onto this window, or use the "Choose File" button below'

        initial_ids = [unicode(id)
                       for id in get_groups_with_perms(self.instance).values_list('id', flat=True)]
        self.fields['worker_permissions'].initial = initial_ids


class ProjectAdmin(GuardedModelAdmin):
    change_form_template = 'admin/turkle/project/change_form.html'
    form = ProjectForm
    formfield_overrides = {
        models.CharField: {'widget': TextInput(attrs={'size': '60'})},
    }
    list_display = ('name', 'filename', 'date_modified', 'active', 'publish_hits')

    # Fieldnames are extracted from form text, and should not be edited directly
    exclude = ('fieldnames',)
    readonly_fields = ('extracted_template_variables',)

    def extracted_template_variables(self, instance):
        return format_html_join('\n', "<li>{}</li>",
                                ((f, ) for f in instance.fieldnames.keys()))

    def get_fieldsets(self, request, obj):
        if not obj:
            # Adding
            return (
                (None, {
                    'fields': ('name', 'assignments_per_hit')
                }),
                ('HTML Template', {
                    'fields': ('html_template', 'template_file_upload', 'filename')
                }),
                ('Permissions', {
                    'fields': ('active', 'login_required', 'custom_permissions',
                               'worker_permissions')
                }),
            )
        else:
            # Changing
            return (
                (None, {
                    'fields': ('name', 'assignments_per_hit')
                }),
                ('HTML Template', {
                    'fields': ('html_template', 'template_file_upload', 'filename',
                               'extracted_template_variables')
                }),
                ('Permissions', {
                    'fields': ('active', 'login_required', 'custom_permissions',
                               'worker_permissions')
                }),
            )

    def publish_hits(self, instance):
        publish_hits_url = '%s?project=%d&assignments_per_hit=%d' % (
            reverse('admin:turkle_batch_add'),
            instance.id,
            instance.assignments_per_hit)
        return format_html('<a href="{}" class="button">Publish Tasks</a>'.
                           format(publish_hits_url))
    publish_hits.short_description = 'Publish Tasks'

    def save_model(self, request, obj, form, change):
        super(ProjectAdmin, self).save_model(request, obj, form, change)
        if u'custom_permissions' in form.data:
            if u'worker_permissions' in form.data:
                existing_groups = set(get_groups_with_perms(obj))
                form_groups = set(form.cleaned_data[u'worker_permissions'])
                groups_to_add = form_groups.difference(existing_groups)
                groups_to_remove = existing_groups.difference(form_groups)
                for group in groups_to_add:
                    assign_perm('can_work_on', group, obj)
                for group in groups_to_remove:
                    remove_perm('can_work_on', group, obj)
            else:
                for group in get_groups_with_perms(obj):
                    remove_perm('can_work_on', group, obj)


admin_site = TurkleAdminSite(name='turkle_admin')
admin_site.register(Group, CustomGroupAdmin)
admin_site.register(User, CustomUserAdmin)
admin_site.register(Batch, BatchAdmin)
admin_site.register(Project, ProjectAdmin)
