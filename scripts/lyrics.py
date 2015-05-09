#!/usr/bin/env python

import argparse
import json
import os
import subprocess
import sys
import textwrap
from itertools import chain


CLASSES = {
    "ladainha": "Ladainha",
    "angola": "Angola",
    "benguela": "Benguela",
    "saobento": "Sao Bento",
    "regional": "Regional",
}


def walk(root, action, format, meta):
    def recurse(x):
        if isinstance(x, list):
            return list(elt for item in x for elt in recurse_list_item(item))
        elif isinstance(x, dict):
            return dict((k, recurse(v)) for k, v in x.items())
        else:
            return x

    def recurse_list_item(item):
        if isinstance(item, dict) and 't' in item:
            result = action(item['t'], item['c'], format, meta)
            if result is None:
                yield recurse(item)
            elif isinstance(result, list):
                for x in result:
                    yield recurse(x)
            else:
                yield recurse(result)
        else:
            yield recurse(item)

    return recurse(root)


def iterate_elements(root, callback, format, meta):
    def recurse(x):
        if isinstance(x, list):
            collect = (elt for item in x for elt in recurse_list_item(item))
        elif isinstance(x, dict):
            collect = (elt for v in x.values() for elt in recurse(v))
        else:
            collect = None
        if collect is not None:
            for elt in collect:
                yield elt

    def recurse_list_item(item):
        if isinstance(item, dict) and 't' in item:
            result = callback(item['t'], item['c'], format, meta)
            if result is not None:
                yield result
        else:
            for elt in recurse(item):
                yield elt

    return recurse(root)


def def_elts(**args):
    current_module = __import__(__name__)

    def def_elt(elt_type, numargs):
        gen = lambda cs: dict(t=elt_type, c=cs)
        if numargs == None:
            build = lambda *xs: gen(xs)
        elif numargs == 0:
            build = lambda: gen([])
        elif numargs == 1:
            build = lambda x: gen(x)
        else:
            def build(*args):
                if len(args) != numargs:
                    raise ValueError(
                        "{} expects {} arguments, but {} given".format(
                            elt_type,
                            numargs,
                            len(args)))
                return gen(args)

        def check(k, *rest):
            return k == elt_type

        build.check = check
        build.name = elt_type
        setattr(current_module, elt_type, build)

    for k, v in args.items():
        def_elt(k, v)


def_elts(
    Header=3,
    RawBlock=2,
    RawInline=2,
    Str=1,
    Link=2,
    Para=None,
)

def LaTexBlock(x):
    return RawBlock('latex', x)

def TexInline(x):
    return RawInline('tex', x)


def run_pred(p, args):
    if isinstance(p, str):
        p = is_type(p)
    return p(*args)


class ElementFilter(object):
    def __init__(self, fn):
        self.fn = fn

    def __call__(self, doc, format, meta):
        return [
            doc[0],
            walk(doc[1], self.fn, format, meta)
        ]


def element_filter(fn):
    ef = ElementFilter(fn)
    ef.__name__ = fn.__name__
    return ef


def remove_if(pred):
    return ElementFilter(lambda *k: [] if run_pred(pred, k) else None)


def is_type(t):
    return lambda key, *rest: key == t


def is_empty(key, value, *k):
    return len(value) == 0


def elem(name, f):
    return ElementFilter(lambda key, *k: f(key, *k) if key == name else None)


def with_pred(fn):
    return lambda *preds: lambda *k: fn(run_pred(p, k) for p in preds)


def mk_filter(*filters):
    def go(doc, format=""):
        for f in filters:
            doc = f(doc, format, doc[0]['unMeta'])
            if doc is None:
                break
        return doc
    return go


def cpy(key, value, *k):
    return dict(t=key, c=value)


def with_latex_env(name):
    def go(*k):
        return [
            LaTexBlock('\\begin{{{}}}'.format(name)),
            cpy(*k),
            LaTexBlock('\\end{{{}}}'.format(name)),
        ]
    return go


def latex_classify(key, value, *ks):
    classes = [cl
               for cl in (CLASSES.get(cl) for cl in value[1][1])
               if cl is not None]
    if classes:
        classes = ', '.join(sorted(classes))
        return Header(
            value[0],
            value[1],
            value[2] + [TexInline('\\newline\\tiny\\color{black!55}' + classes)]
        )


def latex_title_footer(key, value, *ks):
    content = value[2]
    footer = [TexInline('\\rfoot{{\\footnotesize ')] + content + [TexInline('}}')]
    return [cpy(key, value),
            Para(*footer)]


def splice_before(fn):
    def go(*k):
        v = fn(*k)
        if v is None:
            return None
        elif isinstance(v, list):
            return v + [cpy(*k)]
        else:
            return [v, cpy(*k)]
    return ElementFilter(go)


def page_columns(key, value, format, meta):
    COL_CFG = {
        '1': '\\onecolumn',
        '2': '\\twocolumn',
    }

    if  key == 'Header':
        attrs = dict(value[1][2])
        return RawBlock('latex', COL_CFG[attrs.get('columns', '2')])


def collect_headers(doc, format='', meta=[]):
    return iterate_elements(
        doc[1],
        lambda k, v, *rest: v if Header.check(k) else None,
        format,
        meta)


def filter_hidden(doc, format, meta):
    for header in collect_headers(doc, format, meta):
        classes = header[1][1]
        if 'hidden' in classes:
            return None
    return doc


all_pred = with_pred(all)
any_pred = with_pred(any)
latex_same_page = with_latex_env('samepage')
doc_filter = mk_filter(filter_hidden,
                       # remove_if('Link'),
                       remove_if(all_pred('Para', is_empty)),
                       elem('Para', latex_same_page),
                       elem(Header.name, latex_title_footer),
                       elem(Header.name, latex_classify),
                       splice_before(page_columns))


def iflatten(it):
    return (x for xs in it for x in xs)


def dict_groupby(data, keyfunc, datafunc=None):
    d = {}
    extract_data = datafunc if datafunc is not None else lambda x: x
    for elem in data:
        key = keyfunc(elem)
        lst = d.get(key)
        if lst is None:
            lst = []
            d[key] = lst
        lst.append(extract_data(elem))
    return d


def process_file(path):
    pandoc = subprocess.Popen(
        ['pandoc',
         '-f', 'markdown+hard_line_breaks+header_attributes',
         '-t', 'json',
         path,
        ],
        stdout=subprocess.PIPE)
    doc_ast = json.loads(pandoc.stdout.read())
    doc = doc_filter(doc_ast)
    if doc is not None:
        hdr_info = ((hdr[1][0], hdr[1][1], hdr[2])
                    for hdr in collect_headers(doc_ast))
        tags = ((tag, link, title)
                for link, tags, title in hdr_info
                for tag in tags
                if tag in CLASSES)
        return tags, doc[1]
    else:
        return [], []


def iter_filenames(root):
    for folder, subs, files in os.walk(root):
        for file in files:
            yield os.path.join(folder, file)


def gen_toc(title, tags):
    def gen_entry(link, title):
        return Para(Link(title, ['#' + link, '']))

    return chain(
        [LaTexBlock('\\twocolumn'),
         Header(1, [title, [], []], [Str(title)])],
        (gen_entry(*t) for t in tags),
        [LaTexBlock('\\newpage')])


def main(args):
    use_pandoc = args.to != 'json'

    md_files = (path
                for path in iter_filenames(args.path)
                if os.path.splitext(path)[1] == '.md')
    docs = [process_file(path) for path in md_files]
    tags = dict_groupby(iflatten(tags for tags, _ in docs),
                        lambda x: x[0],
                        lambda x: (x[1], x[2]))
    tocs = (gen_toc(cl, tags[k])
            for cl in sorted(CLASSES.values())
            for k, v in CLASSES.items()
            if v == cl)
    elems = chain(iflatten(tocs), iflatten(elems for _, elems in docs))

    if use_pandoc:
        pandoc_args = [
            'pandoc',
            '-s',
            '--template', 'template.latex',
            '-f', 'json',
            '-t', 'latex',
            '-V', 'documentclass=extarticle',
            '-V', 'fontsize:12pt',
            '-V', 'linkcolor:black',
            '-V', 'geometry:a5paper',
            '-V', 'geometry:landscape',
            '-V', 'geometry:headheight=0cm',
            '-V', 'geometry:footskip=1.1cm',
            '-V', 'geometry:lmargin=1cm',
            '-V', 'geometry:rmargin=1cm',
            '-V', 'geometry:tmargin=2.5cm',
            '-V', 'geometry:bmargin=1.2cm',
        ]
        if args.out:
            pandoc_args += ['-o', args.out]

        pandoc = subprocess.Popen(
            pandoc_args,
            stdin=subprocess.PIPE
        )

        out = pandoc.stdin
    else:
        pandoc = None
        out = sys.stdout
    out.write('[{"unMeta": {}},[\n')

    json.dump(elems.next(), out, indent=True)
    for elem in elems:
        out.write(',\n')
        json.dump(elem, out, indent=True)

    out.write('\n]]')
    out.close()

    if pandoc is not None:
        pandoc.wait()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('path', help='lyric source path')
    parser.add_argument('--to', '-t', default='pdf')
    parser.add_argument('--out', '-o', help='pdf target file name')
    args = parser.parse_args()
    main(args)
