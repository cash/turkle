from django.http import HttpResponse
from django.shortcuts import render_to_response, get_object_or_404
from django.template import Context, loader, RequestContext


def view(request, doc_id):
    text = get_text(doc_id)
    c = Context({'text': text})
    t = loader.get_template('doc.html')
    return HttpResponse(t.render(c))

def get_text(doc_id):
    filename = 'data/docs/{}.txt'.format(doc_id)

    text = None
    with open(filename, 'r') as fp:
        text = fp.read()
    if not text:
        text = "Unknown document"

    return text

def paragraphify(text):
    return '<p>' + text.replace("\n", '</p><p>') + '</p>'