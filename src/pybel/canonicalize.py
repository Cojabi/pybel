from __future__ import print_function

import itertools as itt
import sys
from operator import itemgetter

from .constants import ACTIVITY, DEGRADATION, TRANSLOCATION
from .constants import BLACKLIST_EDGE_ATTRIBUTES, CITATION_ENTRIES, EVIDENCE
from .constants import GENEVARIANT, RNAVARIANT, PROTEINVARIANT, MIRNAVARIANT, ABUNDANCE, GENE, MIRNA, PROTEIN, RNA, \
    BIOPROCESS, PATHOLOGY, COMPOSITE, COMPLEX, REACTION
from .constants import GMOD, PMOD, HGVS, KIND, FRAGMENT, FUNCTION, PYBEL_DEFAULT_NAMESPACE
from .constants import GOCC_LATEST, GOCC_KEYWORD, VARIANTS, GENE_FUSION, RNA_FUSION, PROTEIN_FUSION
from .constants import RELATION, PARTNER_3P, PARTNER_5P, RANGE_3P, RANGE_5P, FROM_LOC, TO_LOC, EFFECT, MODIFIER, \
    LOCATION, NAME, NAMESPACE
from .parser.language import inv_document_keys, rev_abundance_labels, unqualified_edges
from .parser.parse_abundance_modifier import PmodParser, GmodParser, FragmentParser, VariantParser
from .parser.utils import ensure_quotes

__all__ = ['to_bel']

variant_parent_dict = {
    GENEVARIANT: 'g',
    RNAVARIANT: 'r',
    PROTEINVARIANT: 'p',
    MIRNAVARIANT: 'm',
    GENE: 'g',
    RNA: 'r',
    PROTEIN: 'p',
    MIRNA: 'm'
}

fusion_parent_dict = {
    GENE_FUSION: 'g',
    RNA_FUSION: 'r',
    PROTEIN_FUSION: 'p'
}


def get_neighbors_by_path_type(g, v, relation):
    """Gets the set of neighbors of a given node that have a relation of the given type

    :param g: A BEL network
    :type g: :class:`pybel.BELGraph`
    :param v: a node from the BEL network
    :param relation: the relation to follow from the given node
    :return:
    """
    result = []
    for neighbor in g.edge[v]:
        for data in g.edge[v][neighbor].values():
            if data[RELATION] == relation:
                result.append(neighbor)
    return set(result)


def postpend_location(s, location_model):
    """Rips off the closing parentheses and adds canonicalized modification.

    I did this because writing a whole new parsing model for the data would be sad and difficult

    :param s:
    :type s: BEL string representing node
    :param location_model:
    :return:
    """

    if all(k in location_model for k in {NAMESPACE, NAME}):
        return "loc({}:{})".format(location_model[NAMESPACE], ensure_quotes(location_model[NAME]))
    raise ValueError('Location model missing namespace and/or name keys: {}'.format(location_model))


def decanonicalize_variant(tokens):
    if tokens[KIND] == PMOD:
        if tokens[PmodParser.IDENTIFIER][NAMESPACE] == PYBEL_DEFAULT_NAMESPACE:
            name = tokens[PmodParser.IDENTIFIER][NAME]
        else:
            name = '{}:{}'.format(tokens[PmodParser.IDENTIFIER][NAMESPACE], tokens[PmodParser.IDENTIFIER][NAME])
        return 'pmod({}{})'.format(name, ''.join(', {}'.format(tokens[x]) for x in PmodParser.ORDER[2:] if x in tokens))
    elif tokens[KIND] == GMOD:
        if tokens[GmodParser.IDENTIFIER][NAMESPACE] == PYBEL_DEFAULT_NAMESPACE:
            name = tokens[GmodParser.IDENTIFIER][NAME]
        else:
            name = '{}:{}'.format(tokens[PmodParser.IDENTIFIER][NAMESPACE], tokens[PmodParser.IDENTIFIER][NAME])
        return 'gmod({})'.format(name)
    elif tokens[KIND] == HGVS:
        return 'var({})'.format(tokens[VariantParser.IDENTIFIER])
    elif tokens[KIND] == FRAGMENT:
        if FragmentParser.MISSING in tokens:
            res = 'frag(?'
        else:
            res = 'frag({}_{}'.format(tokens[FragmentParser.START], tokens[FragmentParser.STOP])

        if FragmentParser.DESCRIPTION in tokens:
            res += ', {}'.format(tokens[FragmentParser.DESCRIPTION])

        return res + ')'


def decanonicalize_fusion_range(tokens):
    if '?' == tokens[0]:
        return '?'
    return '{}.{}_{}'.format(tokens[0], tokens[1], tokens[2])


def decanonicalize_node(g, v):
    """Returns a node from a graph as a BEL string

    :param g: a BEL network
    :type g: :class:`pybel.BELGraph`
    :param v: a node from the BEL graph
    """
    data = g.node[v]

    if data[FUNCTION] == REACTION:
        reactants = get_neighbors_by_path_type(g, v, 'hasReactant')
        reactants_canon = map(lambda n: decanonicalize_node(g, n), sorted(reactants))
        products = get_neighbors_by_path_type(g, v, 'hasProduct')
        products_canon = map(lambda n: decanonicalize_node(g, n), sorted(products))
        return 'rxn(reactants({}), products({}))'.format(', '.join(reactants_canon), ', '.join(products_canon))

    if data[FUNCTION] in (COMPOSITE, COMPLEX) and NAMESPACE not in data:
        members_canon = map(lambda n: decanonicalize_node(g, n), v[1:])
        return '{}({})'.format(rev_abundance_labels[data[FUNCTION]], ', '.join(members_canon))

    if 'variants' in data:
        variants = ', '.join(sorted(map(decanonicalize_variant, data[VARIANTS])))
        return "{}({}:{}, {})".format(variant_parent_dict[data[FUNCTION]],
                                      data[NAMESPACE],
                                      ensure_quotes(data[NAME]),
                                      variants)

    if data[FUNCTION] in (GENE, RNA, MIRNA, PROTEIN, ABUNDANCE, COMPLEX, PATHOLOGY, BIOPROCESS):
        return "{}({}:{})".format(rev_abundance_labels[data[FUNCTION]],
                                  data[NAMESPACE],
                                  ensure_quotes(data[NAME]))

    if data[FUNCTION].endswith('Fusion'):
        return "{}(fus({}:{}, {}, {}:{}, {}))".format(
            fusion_parent_dict[data[FUNCTION]],
            data[PARTNER_5P][NAMESPACE],
            data[PARTNER_5P][NAME],
            decanonicalize_fusion_range(data[RANGE_5P]),
            data[PARTNER_3P][NAMESPACE],
            data[PARTNER_3P][NAME],
            decanonicalize_fusion_range(data[RANGE_3P])
        )

    raise ValueError('Unknown node data: {} {}'.format(v, data))


def decanonicalize_edge_node(g, node, edge_data, node_position):
    node_str = decanonicalize_node(g, node)

    if node_position not in edge_data:
        return node_str

    node_edge_data = edge_data[node_position]

    if LOCATION in node_edge_data:
        node_str = postpend_location(node_str, node_edge_data[LOCATION])

    if MODIFIER in node_edge_data and DEGRADATION == node_edge_data[MODIFIER]:
        node_str = "deg({})".format(node_str)
    elif MODIFIER in node_edge_data and ACTIVITY == node_edge_data[MODIFIER]:
        node_str = "act({}".format(node_str)
        if EFFECT in node_edge_data and node_edge_data[EFFECT]:
            ma = node_edge_data[EFFECT]

            if ma[NAMESPACE] == PYBEL_DEFAULT_NAMESPACE:
                node_str = "{}, ma({}))".format(node_str, ma[NAME])
            else:
                node_str = "{}, ma({}:{}))".format(node_str, ma[NAMESPACE], ensure_quotes(ma[NAME]))
        else:
            node_str = "{})".format(node_str)

    elif MODIFIER in node_edge_data and TRANSLOCATION == node_edge_data[MODIFIER]:
        fromLoc = "fromLoc("
        toLoc = "toLoc("

        if not isinstance(node_edge_data[EFFECT][FROM_LOC], dict):
            raise ValueError()

        fromLoc += "{}:{})".format(node_edge_data[EFFECT][FROM_LOC][NAMESPACE],
                                   ensure_quotes(node_edge_data[EFFECT][FROM_LOC][NAME]))

        if not isinstance(node_edge_data[EFFECT][TO_LOC], dict):
            raise ValueError()

        toLoc += "{}:{})".format(node_edge_data[EFFECT][TO_LOC][NAMESPACE],
                                 ensure_quotes(node_edge_data[EFFECT][TO_LOC][NAME]))

        node_str = "tloc({}, {}, {})".format(node_str, fromLoc, toLoc)

    return node_str


def decanonicalize_edge(g, u, v, k):
    """Takes two nodes and gives back a BEL string representing the statement

    :param g: A BEL graph
    :type g: :class:`BELGraph`
    :param u: The edge's source node
    :param v: The edge's target node
    :param k: The edge's key
    :return: The canonical BEL for this edge
    :rtype: str
    """

    ed = g.edge[u][v][k]

    u_str = decanonicalize_edge_node(g, u, ed, node_position='subject')
    v_str = decanonicalize_edge_node(g, v, ed, node_position='object')

    return "{} {} {}".format(u_str, ed[RELATION], v_str)


def flatten_citation(citation):
    return ','.join('"{}"'.format(citation[x]) for x in CITATION_ENTRIES[:len(citation)])


def sort_edges(d):
    return (flatten_citation(d['citation']), d['SupportingText']) + tuple(
        itt.chain.from_iterable(
            (k, v) for k, v in sorted(d.items(), key=itemgetter(0)) if k not in BLACKLIST_EDGE_ATTRIBUTES))


def to_bel(graph, file=sys.stdout):
    """Outputs the BEL graph as a canonical BEL Script (.bel)

    :param graph: the BEL Graph to output as a BEL Script
    :type graph: BELGraph
    :param file: a filelike object
    :type file: file
    """
    for k in sorted(graph.document):
        print('SET DOCUMENT {} = "{}"'.format(inv_document_keys[k], graph.document[k]), file=file)

    print('###############################################\n', file=file)

    if GOCC_KEYWORD not in graph.namespace_url:
        graph.namespace_url[GOCC_KEYWORD] = GOCC_LATEST

    for namespace, url in sorted(graph.namespace_url.items(), key=itemgetter(0)):
        print('DEFINE NAMESPACE {} AS URL "{}"'.format(namespace, url), file=file)

    for namespace, url in sorted(graph.namespace_owl.items(), key=itemgetter(0)):
        print('DEFINE NAMESPACE {} AS OWL "{}"'.format(namespace, url), file=file)

    print('###############################################\n', file=file)

    for annotation, url in sorted(graph.annotation_url.items(), key=itemgetter(0)):
        print('DEFINE ANNOTATION {} AS URL "{}"'.format(annotation, url), file=file)

    for annotation, an_list in sorted(graph.annotation_list.items(), key=itemgetter(0)):
        an_list_str = ', '.join('"{}"'.format(e) for e in an_list)
        print('DEFINE ANNOTATION {} AS LIST {{{}}}'.format(annotation, an_list_str), file=file)

    print('###############################################\n', file=file)

    # sort by citation, then supporting text
    qualified_edges = filter(lambda u_v_k_d: 'citation' in u_v_k_d[3] and EVIDENCE in u_v_k_d[3],
                             graph.edges_iter(data=True, keys=True))
    qualified_edges = sorted(qualified_edges, key=lambda u_v_k_d: sort_edges(u_v_k_d[3]))

    for citation, citation_edges in itt.groupby(qualified_edges, key=lambda t: flatten_citation(t[3]['citation'])):
        print('SET Citation = {{{}}}'.format(citation), file=file)

        for evidence, evidence_edges in itt.groupby(citation_edges, key=lambda u_v_k_d: u_v_k_d[3][EVIDENCE]):
            print('SET SupportingText = "{}"'.format(evidence), file=file)

            for u, v, k, d in evidence_edges:
                dkeys = sorted(dk for dk in d if dk not in BLACKLIST_EDGE_ATTRIBUTES)
                for dk in dkeys:
                    print('SET {} = "{}"'.format(dk, d[dk]), file=file)
                print(decanonicalize_edge(graph, u, v, k), file=file)
                if dkeys:
                    print('UNSET {{{}}}'.format(', '.join('"{}"'.format(dk) for dk in dkeys)), file=file)
            print('UNSET SupportingText', file=file)
        print('\n', file=file)

    print('###############################################\n', file=file)

    print('SET Citation = {"Other","Added by PyBEL","https://github.com/pybel/pybel/"}', file=file)
    print('SET Evidence = "Automatically added by PyBEL"', file=file)

    for u in graph.nodes_iter():
        if any(d[RELATION] not in unqualified_edges for v in graph.adj[u] for d in
               graph.edge[u][v].values()):
            continue

        print(decanonicalize_node(graph, u), file=file)

    # Can't infer hasMember relationships, but it's not due to specific evidence or citation
    for u, v, d in graph.edges_iter(relation='hasMember', data=True):
        if EVIDENCE in d:
            continue

        print("{} hasMember {}".format(decanonicalize_node(graph, u), decanonicalize_node(graph, v)), file=file)