"""Target-only native change observer; never reads shapes or recomputes.

Generated Shape output is neutral only inside signalled recompute. The native
fixture must prove these callbacks on the installed FreeCAD version. Missing
notifications fail conservatively, rather than accepting unknown edits.
"""


class DocumentObserver:
    DERIVED = frozenset({"Shape", "FullyConstrained", "SolverMessages"})
    PRESENTATION = frozenset({"Label", "Label2", "Visibility"})

    def __init__(self, controller):
        self.controller = controller

    def _revision(self, doc):
        revision = self.controller.revision
        if revision and doc and doc.Name == revision.name:
            return revision
        return None

    def slotChangedObject(self, obj, prop):
        revision = self._revision(obj.Document)
        if revision and prop != 'Visibility':
            revision.changed(f"{obj.Name}.{prop}", derived=prop in self.DERIVED,
                             metadata=prop in self.PRESENTATION)

    def slotChangedDocument(self, doc, prop):
        revision = self._revision(doc)
        if revision and prop not in {'FileName', 'TransientDir'}:
            revision.changed('document.' + prop, metadata=True)

    def slotCreatedObject(self, obj):
        revision = self._revision(obj.Document)
        if revision:
            revision.changed("created " + obj.Name)

    def slotDeletedObject(self, obj):
        revision = self._revision(obj.Document)
        if revision:
            revision.changed("deleted " + obj.Name)

    def slotDeletedDocument(self, doc):
        revision = self._revision(doc)
        if revision:
            revision.closed = True
            revision.changed("target document closed")

    def slotBeforeRecomputeDocument(self, doc):
        revision = self._revision(doc)
        if revision:
            revision.recompute_depth += 1

    def slotRecomputedDocument(self, doc):
        revision = self._revision(doc)
        if revision:
            revision.recompute_depth = 0
