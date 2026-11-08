# minimal stand-in for pytest: parametrize + importorskip only
class _Mark:
    def parametrize(self, names, values):
        names=[n.strip() for n in names.split(',')]
        def deco(fn):
            fn._params=(names,values); return fn
        return deco
mark=_Mark()
def importorskip(name):
    return __import__(name)
