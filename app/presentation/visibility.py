"""读取选择窗格的对象隐藏状态，包括隐藏组合中的子对象。"""


P = 'http://schemas.openxmlformats.org/presentationml/2006/main'
NS = {'p': P}


def hidden_shape_ids(slide):
    """仅依据对象隐藏标记，不推测遮挡、透明、页外或入场动画的可见性。"""
    hidden = set()
    tree = slide.find('p:cSld/p:spTree', NS)
    if tree is None:
        return hidden

    def visit(shape, inherited=False):
        properties = shape.find('*/p:cNvPr', NS)
        invisible = inherited or (properties is not None and properties.get('hidden') in ('1', 'true'))
        if invisible and properties is not None and properties.get('id'):
            hidden.add(properties.get('id'))
        if shape.tag == f'{{{P}}}grpSp':
            for child in shape:
                visit(child, invisible)

    for shape in tree:
        visit(shape)
    return hidden


def targets_hidden_shapes(node, hidden):
    """缺少或混合目标不能当作全部隐藏，以免漏算实际点击。"""
    targets = node.findall('.//p:spTgt', NS)
    return bool(targets) and all(target.get('spid') in hidden for target in targets)
