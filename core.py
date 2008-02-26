import os # for building the location of the .omega/omega_compiled cache directory
import sys # for adding the inline code cache to the include path
import platform #
import unittest
import weakref
import inspect
import md5
import copy

import numpy
from scipy import weave

import gof
from gof import current_mode, set_mode, build_mode, eval_mode, build_eval_mode
from gof import pop_mode, is_result, ResultBase

import type_spec
import cutils
import blas
import compile


# __all__ = ['set_mode', 'get_mode', 'NumpyR', 'NumpyOp']


def build(f, *args, **kwargs):
    build_mode()
    r = f(*args, **kwargs)
    pop_mode()
    return r

def as_string(*rs):
    s = gof.graph.as_string(gof.graph.inputs(rs), rs)
    if len(rs) == 1:
        return s[1:-1]
    else:
        return s

def print_graph(*rs):
    print as_string(*rs)

#useful mostly for unit tests
def _approx_eq(a,b,eps=1.0e-9):
    a = numpy.asarray(a)
    b = numpy.asarray(b)
    if a.shape != b.shape:
        return False
    return numpy.max(numpy.abs(a-b)) < eps


# This function is only executed the first time it is called, subsequent calls 
# return immediately from a cache of the first return value
@blas._constant # TODO: move this decorator to a utility file
def _compile_dir():
    """Return the directory in which scipy.weave should store code objects.

    If the environment variable OMEGA_COMPILEDIR is set, its value is returned.
    If not, a directory of the form $HOME/.omega/compiledir_<platform Id>.

    As a test, this function touches the file __init__.py in the returned
    directory, and raises OSError if there's a problem.

    A directory coming from OMEGA_COMPILEDIR is not created automatically, but
    a directory in $HOME/.omega is created automatically.

    This directory is appended to the sys.path search path before being
    returned, if the touch was successful.
    """
    if os.getenv('OMEGA_COMPILEDIR'):
        cachedir = os.getenv('OMEGA_COMPILEDIR')
    else:
        # use (and possibly create) a default code cache location
        platform_id = platform.platform() + '-' + platform.processor()
        import re
        platform_id = re.sub("[\(\)\s]+", "_", platform_id)
        cachedir = os.path.join(os.getenv('HOME'), '.omega', 'compiledir_'+platform_id)
        if not os.access(cachedir, os.R_OK | os.W_OK):
            #this may raise a number of problems, I think all of which are serious.
            os.makedirs(cachedir, 7<<6)
    cachedir_init = cachedir+'/__init__.py'
    touch = os.system('touch '+cachedir_init)
    if touch:
        raise OSError('touch %s returned %i' % (cachedir_init, touch))

    if cachedir not in sys.path:
        sys.path.append(cachedir)
    return cachedir

class Numpy2(ResultBase):
    """Result storing a numpy ndarray"""
    __slots__ = ['_dtype', '_shape', '_order']

    class ShapeUnknown: pass # TODO: use this as the shape of uncomputed ndarrays of unknown shape
    class StateError(Exception): pass

    def __init__(self, role=None, data=None, constant=False):
        self._order = 'C'
        if isinstance(data, (tuple, list)): # unallocated setup
            shape, dtype = data
            ResultBase.__init__(self, role, data=None, constant=constant)
            self._shape = shape
            self._dtype = dtype
        else:                               # allocated setup
            ResultBase.__init__(self, role, data, constant)

    ################################
    # ResultBase
    # 
    def data_filter(self, data):
        return numpy.asarray(data)
        

    ################################
    # Numpy2 specific functionality
    #
    __array__ = property(lambda self: self.data.__array__)
    __array_struct__ = property(lambda self: self.data.__array_struct__)

    def data_alloc(self):
        return numpy.ndarray(shape=self.shape, dtype=self.dtype, order=self._order)

    # self._dtype is used when self.data hasn't been set yet
    def __dtype_get(self):
        if self.data is not None:
            self._dtype = self.data.dtype
        return self._dtype
    def __dtype_set(self, dtype):
        if self.data is None:
            self._dtype = dtype
        else:
            raise StateError('cannot set dtype after data has been set')
    dtype = property(__dtype_get, __dtype_set)

    # self._shape is used when self.data hasn't been set yet
    def __shape_get(self):
        if self.data is not None:
            self._shape = self.data.shape
        return self._shape
    def __shape_set(self, shape):
        if self.data is None:
            self._shape = shape
        else:
            raise StateError('cannot set shape after data has been set')
    shape = property(__shape_get, __shape_set)

    def  __add__(self, y): return add(self, y)
    def __radd__(self, x): return add(x, self)
    def __iadd__(self, y): return add_inplace(self, y)
    
    def  __sub__(self, y): return sub(self, y)
    def __rsub__(self, x): return sub(x, self)
    def __isub__(self, y): return sub_inplace(self, y)
    
    def  __mul__(self, y): return mul(self, y)
    def __rmul__(self, x): return mul(x, self)
    def __imul__(self, y): return mul_inplace(self, y)
 
    def  __div__(self, y): return div(self, y)
    def __rdiv__(self, x): return div(x, self)
    def __idiv__(self, y): return div_inplace(self, y)
        
    def  __pow__(self, y): return pow(self, y)
    def __rpow__(self, x): return pow(x, self)
    def __ipow__(self, y): return pow_inplace(self, y)

    def __neg__(self):     return neg(self)

    T  = property(lambda self: transpose(self))
    Tc = property(lambda self: transpose_copy(self))

    def __copy__(self):    return array_copy(self)

    def __getitem__(self, item): return get_slice(self, item)
    def __getslice__(self, *args): return get_slice(self, slice(*args))

    #################
    # NumpyR Compatibility
    #
    spec = property(lambda self: (numpy.ndarray, self.dtype, self.shape))
    def set_value_inplace(self, value):
        if 0 == len(self.shape):
            self.data.itemset(value) # for scalars
        else:
            self.data[:] = value     # for matrices
        self.state = gof.result.Computed

class _test_Numpy2(unittest.TestCase):
    def setUp(self):
        build_eval_mode()
        numpy.random.seed(44)
    def tearDown(self):
        pop_mode()
    def test_0(self):
        r = Numpy2()
    def test_1(self):
        o = numpy.ones((3,3))
        r = Numpy2(data=o)
        self.failUnless(r.data is o)
        self.failUnless(r.shape == (3,3))
        self.failUnless(str(r.dtype) == 'float64')

    def test_2(self):
        r = Numpy2(data=[(3,3),'int32'])
        self.failUnless(r.data is None)
        self.failUnless(r.shape == (3,3))
        self.failUnless(str(r.dtype) == 'int32')
        r.alloc()
        self.failUnless(isinstance(r.data, numpy.ndarray))
        self.failUnless(r.shape == (3,3))
        self.failUnless(str(r.dtype) == 'int32')

    def test_3(self):
        a = Numpy2(data=numpy.ones((2,2)))
        b = Numpy2(data=numpy.ones((2,2)))
        c = add(a,b)
        self.failUnless(_approx_eq(c, numpy.ones((2,2))*2))

    def test_4(self):
        ones = numpy.ones((2,2))
        a = Numpy2(data=ones)
        o = numpy.asarray(a)
        self.failUnless((ones == o).all())

    def test_5(self):
        ones = numpy.ones((2,2))
        self.failUnless(_approx_eq(Numpy2(data=ones), Numpy2(data=ones)))


def input(x):
    #static member initialization
    if not hasattr(input, 'float_dtype'):
        input.float_dtype = 'float64'
        input.int_dtype = 'int64'
        input.NN = Numpy2

    if isinstance(x, numpy.ndarray):
        #return NumpyR(x)
        return input.NN(data=x)
    elif isinstance(x, int):
        z = numpy.zeros((), dtype = input.int_dtype)
        z += x
        return input.NN(data=z)
    elif isinstance(x, float):
        z = numpy.zeros((), dtype = input.float_dtype)
        z += x
        return input.NN(data=z)
    elif is_result(x):
        raise TypeError("%s is already a result." % x)
    else:
        return ResultBase(data=x)
class _testCase_input(unittest.TestCase):
    def setUp(self):
        literal.hdb = {}
        literal.udb = {}
    def test_input_int(self):
        w = input(3)
        self.failUnless(isinstance(w, input.NN))
        self.failUnless(str(w.data.dtype) == input.int_dtype)
        self.failUnless(w.data == 3)
    def test_input_float(self):
        w = input(3.0)
        self.failUnless(isinstance(w, input.NN))
        self.failUnless(str(w.data.dtype) == input.float_dtype)
        self.failUnless(w.data == 3.0)

def wrap(x):
    if isinstance(x, Numpy2):
        return x
    #elif isinstance(x, NumpyR):
        #return x
    elif is_result(x):
        return x
    elif isinstance(x, omega_op):
        return x.out
    else:
        return literal(x)
class _testCase_wrap(unittest.TestCase):
    def setUp(self):
        literal.hdb = {}
        literal.udb = {}
    def test_wrap_int(self):
        w = wrap(3)
        self.failUnless(isinstance(w, input.NN))
        self.failUnless(str(w.data.dtype) == input.int_dtype)
        self.failUnless(w.data == 3)
    def test_wrap_float(self):
        w = wrap(3.0)
        self.failUnless(isinstance(w, input.NN))
        self.failUnless(str(w.data.dtype) == input.float_dtype)
        self.failUnless(w.data == 3.0)

def literal(x):
    """Return a ResultValue instance wrapping a literal."""
    def _hashable(x):
        try:
            x in {}
            return True
        except TypeError: # x is unhashable
            return False

    #static member initialization
    if not hasattr(literal, 'hdb'): 
        literal.hdb = {}
        literal.udb = {}

    if _hashable(x):
        db = literal.hdb
        key = (type(x),x)
    else:
        db = literal.udb
        key = (id(x),)

    if key in db:
        return db[key]
    else:
        rval = input(x)
        rval.constant = True
        db[key] = rval
        return rval
class _testCase_literal(unittest.TestCase):
    def setUp(self):
        literal.hdb = {}
        literal.udb = {}
    def test_int(self):
        w = literal(3)
        self.failUnless(isinstance(w, input.NN))
        self.failUnless(str(w.data.dtype) == input.int_dtype)
        self.failUnless(w.data == 3)

        u = literal(1+2)
        self.failUnless(u is w)

    def test_float(self):
        w = literal(3.0)
        self.failUnless(isinstance(w, input.NN))
        self.failUnless(str(w.data.dtype) == input.float_dtype)
        self.failUnless(w.data == 3.0)

        u = literal(1.0+2.0)
        self.failUnless(u is w)

    def test_mixed(self):
        f = literal(2.0)
        i = literal(2)
        self.failUnless(i is not f)




def cgen(name, behavior, names, vals, converters = None):
    
    def cgetspecs(names, vals, converters):
        d = {}
        assert len(names) == len(vals)
        for name, value in zip(names, vals):
            d[name] = value.data
        specs = weave.ext_tools.assign_variable_types(names, d, type_converters = converters) #, auto_downcast = 0)
        return d, specs

    if not converters:
        converters = type_spec.default
    for converter in converters:
        assert isinstance(converter, type_spec.omega_type_converter_extension)


    d, specs = cgetspecs(names, vals, converters)
    
    template = {}
    template['name'] = name
    template['code'] = behavior
    template['members'] = "".join([spec.struct_members_code() for spec in specs])
    template['support'] = "".join([spec.struct_support_code() for spec in specs])
    template['typedefs'] = "".join([spec.struct_typedefs() for spec in specs])
    template['incref'] = "".join(["Py_INCREF(py_%s);\n" % spec.name for spec in specs if spec.use_ref_count])
    template['decref'] = "".join(["Py_DECREF(py_%s);\n" % spec.name for spec in specs if spec.use_ref_count])

    template['struct_contents'] = """
      %(typedefs)s

      %(members)s

      %(support)s

      void init(void) {
        %(incref)s
      }

      void cleanup(void) {
        %(decref)s
      }

      int execute(void) {
        %(code)s
        return 0;
      }
    """ % template

    template['md5'] = md5.md5(template['struct_contents']).hexdigest()
    template['struct_name'] = "_omega_%(name)s_%(md5)s" % template
    struct = "struct %(struct_name)s { %(struct_contents)s\n};" % template

    static = """
    int %(struct_name)s_executor(%(struct_name)s* self) {
        return self->execute();
    }

    void %(struct_name)s_destructor(void* executor, void* self) {
        ((%(struct_name)s*)self)->cleanup();
        free(self);
    }
    """ % template
    
    code = "%(struct_name)s* __STRUCT_P = new %(struct_name)s();\n" % template
    code += "".join([spec.struct_import_code() for spec in specs])
    code += "__STRUCT_P->init();\n"
    code += "return_val = PyCObject_FromVoidPtrAndDesc((void*)(&%(struct_name)s_executor), __STRUCT_P, %(struct_name)s_destructor);\n" % template

    return d, names, code, struct + static, converters    


class Numpy2Op(gof.lib.PythonOp):
    """What can we do given we are interacting with Numpy2 inputs and outputs"""
    def refresh(self, alloc = True):
        shape = self.refresh_shape()
        dtype = self.refresh_dtype()
        out = self.out

        if out.data is not None \
                and out.shape == shape \
                and out.dtype == dtype:
                    return

        alloc |= out.data is not None

        if alloc: out.data = None
        out.shape = shape
        out.dtype = dtype
        if alloc: out.alloc()

class omega_op(Numpy2Op):

    forbid_broadcast = False

    @staticmethod
    def __clsinit__(cls, name, bases, dct):
        for fname in ['grad', 'c_impl', 'impl']:
            if hasattr(cls, fname):
                gof.make_static(cls, fname)

    def __new__(cls, *inputs):
        inputs = [wrap(input) for input in inputs]
        return Numpy2Op.__new__(cls, *inputs)

    def gen_outputs(self):
        return [Numpy2() for i in xrange(self.nout)]
    
    #TODO: use the version of this code that is in grad.py
    #      requires: eliminating module dependency cycles
    def update_gradient(self, grad_d):
        """Call self.grad() and add the result to grad_d

        This function is called by grad.Grad.bprop() to construct a symbolic gradient graph.

        self.grad is called like this:

            self.grad(*(self.inputs + [grad_d[output] for output in self.outputs]))

        In general, grad() should return a list of ResultValue instances whose
        length matches that of self.inputs, and whose elements are the
        gradients of self.inputs.

        There is a (but often used) special feature in place to automatically
        wrap the return value of grad() in a list if it is a ResultValue instance
        and the op is unary.  This makes many grad implementations a little
        cuter.

        """
        inputgs = self.grad(*(self.inputs + [grad_d[output] for output in self.outputs]))
        if len(self.inputs) == 1 and gof.result.is_result(inputgs):
            inputgs = [inputgs]
        else:
            assert len(inputgs) == len(self.inputs)
        for input, inputg in zip(self.inputs, inputgs):
            grad_d.add(input, inputg)

    def c_code(self, converters = None):
        (inames, onames) = self.variable_names()
        behavior = self._c_impl()
        return cgen(self.__class__.__name__, behavior, inames + onames, self.inputs + self.outputs, converters)

    def c_headers(self):
        return []

    def c_libs(self):
        return []

    def c_support_code(self):
        return ""

    def variable_names(self):
        (inames, onames), _1, _2, _3 = inspect.getargspec(self.c_impl)
        return (inames, onames)

    def _c_impl(self):
        return self.c_impl(self.inputs, self.outputs)

    def c_impl(inputs, outputs):
        raise NotImplementedError()

    def c_compile_args(self):
        # I always used these, but they don't make much improvement
        #'-ffast-math', '-falign-loops=8'
        return ['-O2'] 

    def c_thunk_factory(self):
        self.refresh()
        d, names, code, struct, converters = self.c_code()

        cthunk = object()
        module_name = md5.md5(code).hexdigest()
        mod = weave.ext_tools.ext_module(module_name)
        instantiate = weave.ext_tools.ext_function('instantiate',
                                                   code,
                                                   names,
                                                   local_dict = d,
                                                   global_dict = {},
                                                   type_converters = converters)
        instantiate.customize.add_support_code(self.c_support_code() + struct)
        for arg in self.c_compile_args():
            instantiate.customize.add_extra_compile_arg(arg)
        for header in self.c_headers():
            instantiate.customize.add_header(header)
        for lib in self.c_libs():
            instantiate.customize.add_library(lib)
        #add_library_dir
        
        #print dir(instantiate.customize)
        #print instantiate.customize._library_dirs
        if os.getenv('OMEGA_BLAS_LD_LIBRARY_PATH'):
            instantiate.customize.add_library_dir(os.getenv('OMEGA_BLAS_LD_LIBRARY_PATH'))

        mod.add_function(instantiate)
        mod.compile(location = _compile_dir())
        module = __import__("%s" % (module_name), {}, {}, [module_name])

        def creator():
            return module.instantiate(*[x.data for x in self.inputs + self.outputs])
        return creator

    def c_thunk(self):
        return self.c_thunk_creator()

    def c_perform(self):
        thunk = self.c_thunk()
        cutils.run_cthunk(thunk)


def elemwise_loopcode(loopcode, init_template, next_template, acquire_template, cleanup_template, loop_vars, writable_loop_vars, aliases):
    all_loop_vars = loop_vars + writable_loop_vars

    template = dict(
        init = "".join([init_template % dict(loop_var = loop_var) for loop_var in all_loop_vars]),
        next = "".join([next_template % dict(loop_var = loop_var) for loop_var in all_loop_vars]),
        cleanup = "".join([cleanup_template % dict(loop_var = loop_var) for loop_var in all_loop_vars]),
        idefs = "".join([("%(loop_var)s_dtype %(loop_var)s_i = " + acquire_template + ";\n")
                         % dict(loop_var = loop_var) for loop_var in loop_vars]),
        odefs = "".join([("%(loop_var)s_dtype& %(loop_var)s_i = " + acquire_template + ";\n")
                         % dict(loop_var = loop_var) for loop_var in writable_loop_vars]),
        aliasdefs = "".join(["%(v1)s_dtype %(v1)s_i = %(v2)s_i;\n" % dict(v1=v1, v2=v2)
                             for v1, v2 in aliases.items()]),
        loopcode = loopcode
        )

    code = """
    %(init)s
    while (__elemwise_size--) {
        %(idefs)s
        %(odefs)s
        %(aliasdefs)s
        %(loopcode)s
        %(next)s
    }
    %(cleanup)s
    """ % template

    return code


def elemwise_wrap(beforeloop, inloop, afterloop, loop_vars, writable_loop_vars, aliases):

    check_init = """
    npy_intp nd = %(loop_var)s->nd;
    npy_intp* dims = %(loop_var)s->dimensions;
    npy_intp* dims2;
    """

    check = """
    if (%(loop_var)s->nd != nd) {
        PyErr_SetString(PyExc_ValueError, \"The number of dimensions of the inputs do not match.\");
    }
    dims2 = %(loop_var)s->dimensions;
    for (int i = 0; i < nd; i++) {
        if (dims2[i] != dims[i]) {
            PyErr_SetString(PyExc_ValueError, \"The dimensions of the inputs do not match.\");
            return 1;
        }
    }
    """
    
    general_init = "PyArrayIterObject* %(loop_var)s_iter = (PyArrayIterObject*)PyArray_IterNew((PyObject*)%(loop_var)s);\n"
#         "if (%(loop_var)s_iter == NULL) {\n" \
#         "    PyErr_SetString(PyExc_ValueError, \"Could not make an iterator over variable %(loop_var)s.\");\n" \
#         "    return 1;\n" \
#         "}\n"
    general_next = "PyArray_ITER_NEXT(%(loop_var)s_iter);\n"
    general_acquire = "*((%(loop_var)s_dtype*)%(loop_var)s_iter->dataptr)";
    general_cleanup = "if (%(loop_var)s_iter) Py_DECREF(%(loop_var)s_iter);\n";

    contiguous_init = "%(loop_var)s_dtype* __restrict__ %(loop_var)s_iter = (%(loop_var)s_dtype*)PyArray_DATA(%(loop_var)s);\n"
    contiguous_next = "%(loop_var)s_iter++;\n"
    contiguous_acquire = "*%(loop_var)s_iter"
    contiguous_cleanup = ""
    
    all_loop_vars = loop_vars + writable_loop_vars
    v1 = (loop_vars + writable_loop_vars)[0]
    template = dict(
        v1 = v1,
        check_init = check_init % dict(loop_var = v1),
        check = "\n".join([check % dict(loop_var = loop_var) for loop_var in loop_vars + writable_loop_vars if loop_var is not v1]),
        beforeloop = beforeloop,
        general_loop = elemwise_loopcode(
            inloop,
            general_init, general_next, general_acquire, general_cleanup,
            loop_vars, writable_loop_vars, aliases),
        contiguous_loop = elemwise_loopcode(
            inloop,
            contiguous_init, contiguous_next, contiguous_acquire, contiguous_cleanup,
            loop_vars, writable_loop_vars, aliases),
        contiguity_check = "".join(["all_c_contiguous &= PyArray_ISCARRAY(%(loop_var)s);\n" \
                                    "all_f_contiguous &= PyArray_ISFARRAY(%(loop_var)s);\n" \
                                        % dict(loop_var = loop_var)
                                    for loop_var in all_loop_vars]),
        afterloop = afterloop)
    
    code = """
    {
    %(check_init)s
    %(check)s
    }
    npy_intp __elemwise_size = PyArray_SIZE(%(v1)s);
    %(beforeloop)s
    bool all_c_contiguous = 1;
    bool all_f_contiguous = 1;
    %(contiguity_check)s
    if (all_c_contiguous || all_f_contiguous) {
        %(contiguous_loop)s
    }
    else {
        %(general_loop)s
    }
    %(afterloop)s
    """ % template

    return code


def upcast(dtype, *dtypes):
    z = numpy.zeros((), dtype = dtype)
    for dtype in dtypes:
        z = z + numpy.zeros((), dtype = dtype)
    return z.dtype



class elemwise(omega_op):

    @staticmethod
    def __clsinit__(cls, name, bases, dct):
        for fname in ['c_init', 'c_foreach', 'c_finalize']:
            gof.make_static(cls, fname)

        # make impl, grad, etc. static methods
        omega_op.__clsinit__(cls, name, bases, dct)

    def TOGO_specs(self):
        try:
            return self.specs(*[input.spec for input in self.inputs])
        except NotImplementedError:
            inames, onames = self.variable_names()
            linames, lonames = self.loop_variables()
            for oname in onames:
                if oname not in lonames:
                    raise Exception("cannot infer a specification automatically for variable " \
                                    "%s.%s because it is not part of the elementwise loop - "\
                                    "please override the specs method" % (self.__class__.__name__, oname))
            shape, dtype = None, None
            for iname, input in zip(inames, self.inputs):
                if iname in linames:
                    if input.spec:
                        shape = input.spec[2]
            if shape is None:
                raise Exception("cannot infer a specification automatically for output variables " \
                                "because there is no input variable in the loop from which to get the shape, "\
                                "or their shape is unknown")

            try:
                dtype = upcast(*[input.spec[1]
                                 for iname, input in zip(inames, self.inputs)
                                 if input.spec[0] is numpy.ndarray])
            except IndexError:
                raise Exception("not all numpy inputs are specified")

            dmap = self.destroy_map()

            res = []
            for output in self.outputs:
                inplace_inputs = dmap.get(output, [])
                if inplace_inputs:
                    assert len(inplace_inputs) == 1
                    res.append(inplace_inputs[0].spec)
                else:
                    res.append((numpy.ndarray, dtype, shape))
                    
            if self.nout == 1:
                return res[0]
            else:
                return res
        
    def TOGO_alloc(self, except_list = []):
        dmap = self.destroy_map()
        vmap = self.view_map()

        gof.PythonOp.alloc(self, except_list = except_list + dmap.keys())
        for output, (input, ) in dmap.items():
            if output not in except_list:
                output.set_value(input.data)

    def refresh_shape(self):
        """Make the output have the right stuff"""
        if len(self.outputs) > 1:
            raise NotImplementedError('multiple outputs')

        dmap = self.destroy_map()
        vmap = self.view_map()
        if dmap != {} or vmap != {}:
            raise NotImplementedError('destroys or views confuse things',
                    self.__class__, dmap, vmap)

        # take the shape of the leftmost loop_variable input
        inames, onames = self.variable_names()
        linames, lonames = self.loop_variables()

        unknown_output_names = [n for n in onames if n not in lonames]
        if len(unknown_output_names):
            raise Exception("cannot infer a specification automatically for variables " \
                            "%s.{%s} because it is not part of the elementwise loop - "\
                            "please override the specs method" % 
                            (self.__class__.__name__, str(unknown_output_names)))

        # shape is leftmost loop-variable input
        input_loop_shapes = [i.shape for n,i in zip(inames, self.inputs) if n in linames]
        if len(input_loop_shapes) == 0:
            raise Exception("cannot infer a specification automatically for output variables " \
                            "because there is no input loop variable ")
        for i in xrange(1,len(input_loop_shapes)):
            if  input_loop_shapes[i] != input_loop_shapes[0]:
                raise Exception("Input loop variables have different shapes", self.__class__)

        return input_loop_shapes[0]

    def refresh_dtype(self):
        return upcast(*[i.dtype for i in self.inputs if hasattr(i, 'dtype')])

    @classmethod
    def set_impl(cls, impl):
        gof.lib.make_static(cls, 'impl')

    @staticmethod
    def is_loop_var(name):
        return name.endswith("_i")

    @staticmethod
    def extract_name(name):
        if name.endswith("_i"):
            return name[:-2]
        else:
            return name

    @classmethod
    def variable_names(cls):
        (inames, onames), _1, _2, _3 = inspect.getargspec(cls.c_foreach)
        spec = ([cls.extract_name(name) for name in inames],
                [cls.extract_name(name) for name in onames])
        if cls.c_init is not elemwise.c_init:
            (inames, onames), _1, _2, _3 = inspect.getargspec(cls.c_init)
            assert spec == (list(inames), list(onames))
        if cls.c_finalize is not elemwise.c_finalize:
            (inames, onames), _1, _2, _3 = inspect.getargspec(cls.c_finalize)
            assert spec == (list(inames), list(onames))
        return spec

    @classmethod
    def loop_variables(cls):
        (inames, onames), _1, _2, _3 = inspect.getargspec(cls.c_foreach)
        return ([cls.extract_name(name) for name in inames if cls.is_loop_var(name)],
                [cls.extract_name(name) for name in onames if cls.is_loop_var(name)])

    def _c_init(self):
        return self.c_init(self.inputs, self.outputs)
        
    def c_init(inputs, outputs):
        return ""

    def _c_foreach(self):
        return self.c_foreach(self.inputs, self.outputs)
        
    def c_foreach(inputs, outputs):
        raise NotImplementedError()

    def _c_finalize(self):
        return self.c_finalize(self.inputs, self.outputs)

    def c_finalize(inputs, outputs):
        return ""

    def c_code(self, converters = None, elemwise_wrap = elemwise_wrap):
        def mangle(name):
            if name.endswith("_i"):
                return name[:-2]
            else:
                return name

        try:
            self._c_impl()
            raise Exception("c_impl is not used by elemwise ops - define behavior in c_foreach instead")
        except NotImplementedError:
            pass

        before = self._c_init()
        during = self._c_foreach()
        after  = self._c_finalize()
        
        (inames, onames) = self.variable_names()
        (linames, lonames) = self.loop_variables()

        aliases = {}
        dmap = self.destroy_map()
        if dmap != {}:
            for oname, output in zip(onames, self.outputs):
                if oname in lonames:
                    for input in dmap.get(output, []):
                        aliases[inames[self.inputs.index(input)]] = oname
                        
        behavior = elemwise_wrap(before, during, after,
                                 [name for name in linames if name not in aliases],
                                 lonames,
                                 aliases)
        
        return cgen(self.__class__.__name__, behavior, inames + onames, self.inputs + self.outputs, converters)

    @classmethod
    def inplace_version(cls, dmap = {0: 0}):
        inames, onames = cls.variable_names()
        linames, lonames = cls.loop_variables()
        for i, oname in enumerate(onames):
            if i in dmap:
                assert oname in lonames
        
        class C(cls):
            def destroy_map(self):
                assert cls.destroy_map(self) == {}
                ret = {}
                for output, input in dmap.items():
                    ret[self.outputs[output]] = [self.inputs[input]]
                return ret
            def _impl(self):
                if self.impl is not cls.impl:
                    # If the user sets his own inplace operation, we use it
                    return cls._impl(self)
                else:
                    res = cls._impl(self)
                    if isinstance(res, (list, tuple)):
                        res = copy.copy(res)
                    else:
                        res = [res]
                    for output, input in dmap.items():

                        # The default implementation returned a copy, so we just
                        # overwrite the original input with the contents of that copy
                        # This is not meant to be efficient, only correct.
                        #
                        # TODO: change this to use set_value_inplace
                        a = self.inputs[input].data
                        a[:] = res[output]
                        res[output] = a
                    if len(res) == 1:
                        return res[0]
                    else:
                        return res

        if dmap == {0:0}:
            C.__name__ = cls.__name__ + "_inplace" % dmap
        else:
            C.__name__ = cls.__name__ + "_inplace%s" % dmap
        return C

def scalar_switch(normal_f, scalar_f, scalar_f_reverse = None):
    def f(x, y):
        x, y = wrap(x), wrap(y)
        if y.constant and not y.data.shape:
            return scalar_f(x, y)
        if x.constant and not x.data.shape:
            if scalar_f_reverse:
                return scalar_f_reverse(y, x)
            else:
                raise TypeError("You cannot do this operation on a scalar.")
        return normal_f(x, y)
    return f


from grad import Undefined

def wrap_producer(f):
    class producer(gof.lib.NewPythonOp):
        def __init__(self, shape, dtype, order):
            assert order == 'C' #TODO: let Numpy2 support this
            if current_mode() == 'build_eval':
                gof.lib.NewPythonOp.__init__(self, 
                        [input(shape), input(dtype), input(order)],
                        [Numpy2(data = f(shape, dtype))])
            elif current_mode() == 'build':
                gof.lib.NewPythonOp.__init__(self, 
                        [input(shape), input(dtype), input(order)],
                        [Numpy2(data = (shape, dtype))])
        def gen_outputs(self):
            return [Numpy2() for i in xrange(self.nout)]
        impl = f
        def grad(*args):
            return [Undefined] * (len(args) - 1)
    producer.__name__ = f.__name__
    def ret(shape, dtype = 'float64', order = 'C'):
        return producer(shape, dtype, order).out
    return ret

ndarray = wrap_producer(numpy.ndarray)
array = wrap_producer(numpy.array)
zeros = wrap_producer(numpy.zeros)
ones = wrap_producer(numpy.ones)

class _testCase_producer_build_mode(unittest.TestCase):
    def test_0(self):
        """producer in build mode"""
        build_mode()
        a = ones(4)
        self.failUnless(a.data is None, a.data)
        self.failUnless(a.state is gof.result.Empty, a.state)
        self.failUnless(a.shape == 4, a.shape)
        self.failUnless(str(a.dtype) == 'float64', a.dtype)
        pop_mode()
    def test_1(self):
        """producer in build_eval mode"""
        build_eval_mode()
        a = ones(4)
        self.failUnless((a.data == numpy.ones(4)).all(), a.data)
        self.failUnless(a.state is gof.result.Computed, a.state)
        self.failUnless(a.shape == (4,), a.shape)
        self.failUnless(str(a.dtype) == 'float64', a.dtype)
        pop_mode()



# Wrapper to ensure that all inputs to the function impl have the same size (foils numpy's broadcasting)
def assert_same_shapes(impl):
    def ret(x, *rest):
        shape = x.shape
        for other in rest:
            if other.shape != shape:
                raise ValueError("The dimensions of the inputs do not match.")
        return impl(x, *rest)
    return ret

# Wrapper to ensure that the last input to impl is a scalar
def tensor_scalar_impl(impl):
    def ret(x, a):
        if a.shape:
            raise ValueError("The second argument to %s must be a scalar." % impl)
        return impl(x, a)
    return ret

class tensor_scalar_op(elemwise):
    @classmethod
    def variable_names(cls):
        return (['x', '_a'], ['z', ])
    @classmethod
    def loop_variables(cls):
        return (['x', ], ['z', ])
    def c_init((x, _a), (z, )):
        return """
        if (PyArray_SIZE(_a) != 1) {
            PyErr_SetString(PyExc_ValueError, \"The size of the scalar argument is not 1.\");
        }
        _a_dtype a = ((_a_dtype*)PyArray_DATA(_a))[0];
        """
    def _c_foreach(self):
        return "z_i = %s;" % self.c_expr



## Addition ##

class add_elemwise(elemwise):
    impl = assert_same_shapes(numpy.ndarray.__add__)
    def grad(x, y, gz):
        return gz, gz
    def c_foreach((x_i, y_i), (z_i, )):
        return "z_i = x_i + y_i;"

add_elemwise_inplace = add_elemwise.inplace_version()
add_elemwise_inplace.set_impl(assert_same_shapes(numpy.ndarray.__iadd__))




class add_scalar(tensor_scalar_op):
    impl = tensor_scalar_impl(numpy.ndarray.__add__)
    def grad(x, a, gz):
        return gz, sum(gz)
    c_expr = "x_i + a"

add_scalar_inplace = add_scalar.inplace_version()
add_scalar_inplace.set_impl(tensor_scalar_impl(numpy.ndarray.__iadd__))

class _testCase_add_build_mode(unittest.TestCase):
    def setUp(self):
        build_mode()
        numpy.random.seed(44)
    def tearDown(self):
        pop_mode()

class twice(elemwise):
    def impl(x):
        return 2.0 * x
    def grad(x, gz):
        return scale(gz, 2.0)
    def c_foreach((x_i, ), (z_i, )):
        "z_i = x_i + x_i;"

twice_inplace = twice.inplace_version()


## Subtraction ##

class sub_elemwise(elemwise):
    impl = assert_same_shapes(numpy.ndarray.__sub__)
    def grad(x, y, gz):
        return gz, -gz
    def c_foreach((x_i, y_i), (z_i, )):
        return "z_i = x_i - y_i;"

sub_elemwise_inplace = sub_elemwise.inplace_version()
sub_elemwise_inplace.set_impl(assert_same_shapes(numpy.ndarray.__isub__))

def sub_scalar_r(x, a):
    return add_scalar(x, -a)

def sub_scalar_l(x, a):
    return add_scalar(-x, a)

def sub_scalar_r_inplace(x, a):
    return add_scalar_inplace(x, -a)


## Element-wise multiplication ##

class mul_elemwise(elemwise):
    impl = assert_same_shapes(numpy.ndarray.__mul__)
    def grad(x, y, gz):
        return mul(y, gz), mul(x, gz)
    def c_foreach((x_i, y_i), (z_i, )):
        return "z_i = x_i * y_i;"

mul_elemwise_inplace = mul_elemwise.inplace_version()
mul_elemwise_inplace.set_impl(assert_same_shapes(numpy.ndarray.__imul__))


class scale(tensor_scalar_op):
    impl = tensor_scalar_impl(numpy.ndarray.__mul__)
    def grad(x, a, gz):
        return scale(a, gz), sum(mul_elemwise(x, gz))
    c_expr = "x_i * a"

scale_inplace = scale.inplace_version()
scale_inplace.set_impl(tensor_scalar_impl(numpy.ndarray.__imul__))


class sqr(elemwise):
    def impl(x):
        return x * x
    def grad(x, gz):
        return scale(mul_elemwise(x, gz), 2.0)
    def c_foreach((x_i, ), (z_i, )):
        return "z_i = x_i * x_i;"

isqr = sqr.inplace_version()
isqr.set_impl(lambda x: x.__imul__(x))



class sqrt(elemwise):
    impl = numpy.sqrt
    def grad(x, gz):
        return scale(div(gz, sqrt(x)), 0.5)
    def c_foreach((x_i, ), (z_i, )):
        return "z_i = pow(x_i, 0.5);"

isqrt = sqrt.inplace_version()
isqrt.set_impl(lambda x: x.__ipow__(0.5))



## Element-wise division ##

class div_elemwise(elemwise):
    impl = assert_same_shapes(numpy.ndarray.__div__)
    def grad(x, y, gz):
        return div(gz, y), -div(mul(x, gz), sqr(y))
    def c_foreach((x_i, y_i), (z_i, )):
        return "z_i = x_i / y_i;"

div_elemwise_inplace = div_elemwise.inplace_version()
div_elemwise_inplace.set_impl(assert_same_shapes(numpy.ndarray.__idiv__))

def div_scalar_r(x, a):
    return scale(x, inv_elemwise(a))

def div_scalar_l(x, a):
    return scale(inv_elemwise(x), a)

def div_scalar_r_inplace(x, a):
    return scale_inplace(x, inv_elemwise(a))



## Scaling ##

class scale(tensor_scalar_op):
    impl = tensor_scalar_impl(numpy.ndarray.__mul__)
    def grad(x, a, gz):
        return scale(a, gz), sum(mul_elemwise(x, gz))
    c_expr = "x_i * a"

scale_inplace = scale.inplace_version()
scale_inplace.set_impl(tensor_scalar_impl(numpy.ndarray.__imul__))


class neg(elemwise):
    impl = numpy.ndarray.__neg__
    def grad(x, gz):
        return -gz
    def c_foreach((x_i, ), (z_i, )):
        return "z_i = -x_i;"

neg_inplace = neg.inplace_version()
neg_inplace.set_impl(lambda x: x.__imul__(-1))


class inv_elemwise(elemwise):
    impl = lambda x: 1 / x
    def grad(x, gz):
        return -gz
    def c_foreach((x_i, ), (z_i, )):
        return "z_i = 1 / x_i;"

inv_elemwise_inplace = inv_elemwise.inplace_version()


## Dot product ##

class dot(omega_op):
    @staticmethod
    def _output_shape(xshape, yshape):
        if len(xshape) == 0: # x is a scalar
            shape = yshape
        else:
            if len(yshape) >= 2: #y is a matrix or tensor
                assert xshape[-1] == yshape[-2]
                shape = tuple(xshape[:-1]+ yshape[:-2]+yshape[-1:])
            elif len(yshape)==1: #y is vector
                assert xshape[-1] == yshape[-1]
                shape = tuple(xshape[:-1])
            else:                #y is a scalar
                shape = xshape
        return shape

    impl = numpy.dot
    def grad(x, y, gz):
        return dot(gz, transpose(y)), dot(transpose(x), gz)
    def refresh(self, alloc=False):
        x,y = self.inputs
        shape = self._output_shape(x.shape, y.shape)
        dtype = upcast(x.dtype, y.dtype)
        if self.out.data is not None \
                and self.out.shape == shape \
                and self.out.dtype == dtype:
                    return  #everything is ok
        if alloc or self.out.data is not None: #data should be allocated
            self.out.data = None
            self.out.shape = shape
            self.out.dtype = dtype
            self.out.alloc()
        else:
            self.out.shape = shape
            self.out.dtype = dtype
    def c_support_code(self):
        return blas.cblas_header_text()
    def c_libs(self):
        return blas.ldflags()
    def c_impl((_x, _y), (_z, )):
        return blas.gemm_code('', '1.0', '0.0')

class _testCase_dot(unittest.TestCase):
    def setUp(self):
        build_eval_mode()
        numpy.random.seed(44)
    def tearDown(self):
        pop_mode()

    @staticmethod
    def rand(*args):
        return numpy.random.rand(*args)

    def cmp_dot(self,x,y):
        def spec(x):
            x = numpy.asarray(x)
            return type(x), x.dtype, x.shape
        zspec = dot.specs(spec(x), spec(y))
        nz = numpy.dot(x,y)
        self.failUnless(zspec == spec(nz))
        self.failUnless(_approx_eq(dot(x,y), numpy.dot(x,y)))

    def cmp_dot_comp(self, x,y):
        x = numpy.asarray(x)
        y = numpy.asarray(y)
        z = dot(x,y)
        p = compile.single(z)
        if len(x.shape):
            x[:] = numpy.random.rand(*x.shape)
        else:
            x.fill(numpy.random.rand(*x.shape))
        if len(y.shape):
            y[:] = numpy.random.rand(*y.shape)
        else:
            y.fill(numpy.random.rand(*y.shape))
        p() # recalculate z
        self.failUnless(_approx_eq(z, numpy.dot(x,y)))

    def test_dot_0d_0d(self): self.cmp_dot(1.1, 2.2)
    def test_dot_0d_1d(self): self.cmp_dot(1.1, self.rand(5))
    def test_dot_0d_2d(self): self.cmp_dot(3.0, self.rand(6,7))
    def test_dot_0d_3d(self): self.cmp_dot(3.0, self.rand(8,6,7))
    def test_dot_1d_0d(self): self.cmp_dot(self.rand(5), 1.1 )
    def test_dot_1d_1d(self): self.cmp_dot(self.rand(5), self.rand(5))
    def test_dot_1d_2d(self): self.cmp_dot(self.rand(6), self.rand(6,7))
    def test_dot_1d_3d(self): self.cmp_dot(self.rand(6), self.rand(8,6,7))
    def test_dot_2d_0d(self): self.cmp_dot(self.rand(5,6), 1.0)
    def test_dot_2d_1d(self): self.cmp_dot(self.rand(5,6), self.rand(6))
    def test_dot_2d_2d(self): self.cmp_dot(self.rand(5,6), self.rand(6,7))
    def test_dot_2d_3d(self): self.cmp_dot(self.rand(5,6), self.rand(8,6,7))
    def test_dot_3d_0d(self): self.cmp_dot(self.rand(4,5,6), 1.0)
    def test_dot_3d_1d(self): self.cmp_dot(self.rand(4,5,6), self.rand(6))
    def test_dot_3d_2d(self): self.cmp_dot(self.rand(4,5,6), self.rand(6,7))
    def test_dot_3d_3d(self): self.cmp_dot(self.rand(4,5,6), self.rand(8,6,7))
    def test_dot_0d_0d_(self): self.cmp_dot_comp(1.1, 2.2)
    def test_dot_0d_1d_(self): self.cmp_dot_comp(1.1, self.rand(5))
    def test_dot_0d_2d_(self): self.cmp_dot_comp(3.0, self.rand(6,7))
    def test_dot_0d_3d_(self): self.cmp_dot_comp(3.0, self.rand(8,6,7))
    def test_dot_1d_0d_(self): self.cmp_dot_comp(self.rand(5), 1.1 )
    def test_dot_1d_1d_(self): self.cmp_dot_comp(self.rand(5), self.rand(5))
    def test_dot_1d_2d_(self): self.cmp_dot_comp(self.rand(6), self.rand(6,7))
    def test_dot_1d_3d_(self): self.cmp_dot_comp(self.rand(6), self.rand(8,6,7))
    def test_dot_2d_0d_(self): self.cmp_dot_comp(self.rand(5,6), 1.0)
    def test_dot_2d_1d_(self): self.cmp_dot_comp(self.rand(5,6), self.rand(6))
    def test_dot_2d_2d_(self): self.cmp_dot_comp(self.rand(5,6), self.rand(6,7))
    def test_dot_2d_3d_(self): self.cmp_dot_comp(self.rand(5,6), self.rand(8,6,7))
    def test_dot_3d_0d_(self): self.cmp_dot_comp(self.rand(4,5,6), 1.0)
    def test_dot_3d_1d_(self): self.cmp_dot_comp(self.rand(4,5,6), self.rand(6))
    def test_dot_3d_2d_(self): self.cmp_dot_comp(self.rand(4,5,6), self.rand(6,7))
    def test_dot_3d_3d_(self): self.cmp_dot_comp(self.rand(4,5,6), self.rand(8,6,7))

    def test_dot_fail_1_1(self):
        x = numpy.random.rand(5)
        y = numpy.random.rand(6)
        try:
            z = dot(x,y)
        except ValueError, e:
            self.failUnless(str(e) == 'objects are not aligned', e)
            return
        self.fail()

    def test_dot_fail_1_2(self):
        x = numpy.random.rand(5)
        y = numpy.random.rand(6,4)
        try:
            z = dot(x,y)
        except ValueError, e:
            self.failUnless(str(e) == 'objects are not aligned', e)
            return
        self.fail()
    def test_dot_fail_1_3(self):
        x = numpy.random.rand(5)
        y = numpy.random.rand(6,4,7)
        try:
            z = dot(x,y)
        except ValueError, e:
            self.failUnless(str(e) == 'objects are not aligned', e)
            return
        self.fail()
    def test_dot_fail_2_1(self):
        x = numpy.random.rand(5,4)
        y = numpy.random.rand(6)
        try:
            z = dot(x,y)
        except ValueError, e:
            self.failUnless(str(e) == 'objects are not aligned', e)
            return
        self.fail()
    def test_dot_fail_2_2(self):
        x = numpy.random.rand(5,4)
        y = numpy.random.rand(6,7)
        try:
            z = dot(x,y)
        except ValueError, e:
            self.failUnless(str(e) == 'objects are not aligned', e)
            return
        self.fail()
    def test_dot_fail_2_3(self):
        x = numpy.random.rand(5,4)
        y = numpy.random.rand(6,7,8)
        try:
            z = dot(x,y)
        except ValueError, e:
            self.failUnless(str(e) == 'objects are not aligned', e)
            return
        self.fail()
    def test_dot_fail_3_1(self):
        x = numpy.random.rand(5,4,3)
        y = numpy.random.rand(6)
        try:
            z = dot(x,y)
        except ValueError, e:
            self.failUnless(str(e) == 'objects are not aligned', e)
            return
        self.fail()
    def test_dot_fail_3_2(self):
        x = numpy.random.rand(5,4,3)
        y = numpy.random.rand(6,7)
        try:
            z = dot(x,y)
        except ValueError, e:
            self.failUnless(str(e) == 'objects are not aligned', e)
            return
        self.fail()
    def test_dot_fail_3_3(self):
        x = numpy.random.rand(5,4,3)
        y = numpy.random.rand(6,7,8)
        try:
            z = dot(x,y)
        except ValueError, e:
            self.failUnless(str(e) == 'objects are not aligned', e)
            return
        self.fail()

class gemm(omega_op):
    def destroy_map(self):
        return {self.out:[self.inputs[0]]}
    def impl(z, a, x, y, b):
        if b == 0.0:
            if a == 1.0:
                z[:] = numpy.dot(x,y)
            elif a == -1.0:
                z[:] = -numpy.dot(x,y)
            else:
                z[:] = a * numpy.dot(x,y)
        elif b == 1.0:
            if a == 1.0:
                z += numpy.dot(x,y)
            elif a == -1.0:
                z -= numpy.dot(x,y)
            else:
                z += a * numpy.dot(x,y)
        else:
            z *= b
            z += a * numpy.dot(x,y)
        return z[:]
    def grad(z, a, x, y, b, gz):
        raise NotImplemented
    def refresh(self, alloc = False):
        z,a,x,y,b = self.inputs
        self.out.shape = z.shape
        self.out.dtype = z.dtype
        if alloc:
            self.out.data = z.data
    def c_support_code(self):
        return blas.cblas_header_text()
    def c_libs(self):
        return blas.ldflags()
    def c_impl((_zin, _a, _x, _y, _b), (_z,)):
        check_ab = """
        {
        if ((_a->descr->type_num != PyArray_DOUBLE)
            && (_a->descr->type_num != PyArray_FLOAT))
            goto _dot_execute_fallback;

        if ((_b->descr->type_num != PyArray_DOUBLE)
            && (_b->descr->type_num != PyArray_FLOAT))
            goto _dot_execute_fallback;
        }
        """
        return blas.gemm_code( check_ab,
                '(_a->descr->type_num == PyArray_FLOAT) ? (REAL)(((float*)_a->data)[0]) : (REAL)(((double*)_a->data)[0])',
                '(_b->descr->type_num == PyArray_FLOAT) ? (REAL)(((float*)_b->data)[0]) : (REAL)(((double*)_b->data)[0])')


## Transposition ##

class transpose(omega_op):
    def view_map(self): return {self.out: [self.inputs[0]]}
    impl = numpy.transpose
    def grad(x, gz):
        return transpose_copy(gz)
    def refresh_shape(self):
        rval = list(self.inputs[0].shape)
        rval.reverse()
        return rval
    def refresh_dtype(self):
        return  self.inputs[0].dtype
    def c_impl((x, ), (xt, )):
        return """
        const int l = x->nd;
        // The user must ensure that all references to
        //xt->data go through xt, or there's going to be trouble..
        int refcheck = 0;

          if (x == xt)
            {
              return -1;
            }
          if (refcheck)
            {
              int refcnt =  PyArray_REFCOUNT(xt);
                if ((refcnt > 2)  // you might think this should be 1.. but this works
                    //|| (xt->base != NULL)
                    || (xt->weakreflist != NULL))
                  {
                    PyErr_SetString(PyExc_ValueError,
                                        "cannot resize an array that has "\\
                                        "been referenced or is referencing\\n"\\
                                        "another array in this way.  Use the "\\
                                        "resize function");
                    return -2;
                  }
            }

        if (xt->nd != x->nd)
        {
            // this technique comes from PyArray_Resize()
            npy_intp * dimptr = (npy_intp*)PyDimMem_RENEW(xt->dimensions, 2 * x->nd);
            if (!dimptr)
            {
                  PyErr_NoMemory();
                  return 1;
            }
            xt->nd = x->nd;
            xt->dimensions = dimptr;
            xt->strides = dimptr + x->nd;
        }
        //copy x's dimensions and strides
        for (int i = 0; i < l; ++i)
        {
            xt->dimensions[i] = x->dimensions[l-i-1];
            xt->strides[i] = x->strides[l-i-1];
        }

        // point directly at b's type descriptor
        Py_INCREF(x->descr);
        Py_DECREF(xt->descr);
        xt->descr = x->descr;

        // name x as a base of xt, increment its refcount
        if ( xt->base != (PyObject*)x)
        {
          Py_INCREF(x);
          if ((xt->base) && (xt->base != Py_None)) 
            {
              Py_DECREF(xt->base);
            }
          xt->base = (PyObject*)x;
        }
    
        // mark xt as not owning its data
        if (PyArray_CHKFLAGS(xt, NPY_OWNDATA))
          {
            PyDataMem_FREE(xt->data);
            xt->flags &= ~NPY_OWNDATA;
          }
        xt->data = x->data;

        // this function is described in 
        // ~/zzz.NOBACKUP/pub/src/numpy-1.0.3.1/numpy/core/src/arrayobject.c:1890
        PyArray_UpdateFlags(xt, NPY_CONTIGUOUS|NPY_FORTRAN|NPY_ALIGNED|NPY_WRITEABLE); 

        /*
          TODO
          What should be done with the weakreflist ?
        */
    """

def transpose_copy(x):
    return array_copy(transpose(x))

class _testCase_transpose(unittest.TestCase):

    def setUp(self):
        build_eval_mode()

    def tearDown(self):
        pop_mode()
    
    def test_1d_alias(self):
        a = numpy.ones(10)
        ta = transpose(a)
        self.failUnless(ta.data.shape == a.shape)
        self.failUnless(numpy.all(ta.data == a))
        a[3] *= -1.0
        self.failUnless(numpy.all(ta.data == a))

    def test_1d_copy(self):
        a = numpy.ones(10)
        ta = transpose_copy(a)
        self.failUnless(ta.data.shape == a.shape)
        self.failUnless(numpy.all(ta.data == a))
        a[3] *= -1.0
        self.failIf(numpy.all(ta.data == a))

    def test_2d_alias(self):
        a = numpy.ones((10,3))
        ta = transpose(a)
        self.failUnless(ta.data.shape == (3,10))

    def test_3d_alias(self):
        a = numpy.ones((10,3,5))
        ta = transpose(a)
        self.failUnless(ta.data.shape == (5,3,10))
        a[9,0,0] = 5.0
        self.failUnless(ta.data[0,0,9] == 5.0)

    def test_3d_copy(self):
        a = numpy.ones((10,3,5))
        ta = transpose_copy(a)
        self.failUnless(ta.data.shape == (5,3,10))
        a[9,0,0] = 5.0
        self.failUnless(ta.data[0,0,9] == 1.0)

## Copy ##

class array_copy(elemwise):
    impl = numpy.array
    grad = lambda x, gz: gz
    def c_foreach((x_i, ), (z_i, )):
        return "z_i = x_i;"


## Power ##

class sqr(elemwise):
    def impl(x):
        return x * x
    def grad(x, gz):
        return scale(mul_elemwise(x, gz), 2.0)
    def c_foreach((x_i, ), (z_i, )):
        return "z_i = x_i * x_i;"

sqr_inplace = sqr.inplace_version()
sqr_inplace.set_impl(lambda x: x.__imul__(x))




class sqrt(elemwise):
    impl = numpy.sqrt
    def grad(x, gz):
        return scale(div(gz, sqrt(x)), 0.5)
    def c_foreach((x_i, ), (z_i, )):
        return "z_i = pow(x_i, 0.5);"

sqrt_inplace = sqrt.inplace_version()
sqrt_inplace.set_impl(lambda x: x.__ipow__(0.5))


class exp(elemwise):
    def impl(x): return numpy.exp(x)
    def grad(x, gz): return gz * exp(x)
    def c_foreach((x_i, ), (z_i, )): return "z_i = exp(x_i);"
    
class log(elemwise):
    def impl(x): return numpy.log(x)
    def grad(x, gz): return gz / x
    def c_foreach((x_i, ), (z_i, )): return "z_i = log(x_i);"

class log2(elemwise):
    def impl(x): return numpy.log2(x)
    def grad(x, gz): return gz / (x * numpy.log(2))
    def c_foreach((x_i, ), (z_i, )): return "z_i = log2(x_i);"

class pow_elemwise(elemwise):
    impl = assert_same_shapes(numpy.ndarray.__pow__)
    def grad(x, s, gz):
        raise NotImplemented # no gs
        return gz * s * (pow_elemwise(x, s-1.0))
    def c_foreach((x_i, s_i), (z_i, )):
        return "z_i = pow(x_i, s_i)"

pow_elemwise_inplace = pow_elemwise.inplace_version()
pow_elemwise_inplace.set_impl(assert_same_shapes(numpy.ndarray.__ipow__))

class pow_scalar_l(tensor_scalar_op):
    impl = tensor_scalar_impl(lambda x, y: numpy.ndarray.__pow__(y, x))
    def grad(x, s, gz):
        raise NotImplemented # no gs
        return gz * x * (pow_scalar_l(s,x-1.0))
    c_expr = "pow(a, x_i)"

class pow_scalar_r(tensor_scalar_op):
    impl = tensor_scalar_impl(numpy.ndarray.__pow__)
    def grad(x, s, gz):
        gx = gz * s * (pow_scalar_r(x,s-1.0))
        gs = sum(gz * pow_scalar_r(x,s) * log(x))
        return gx, gs
    c_expr = "pow(x_i, a)"

pow_scalar_r_inplace = pow_scalar_r.inplace_version()
pow_scalar_r_inplace.set_impl(tensor_scalar_impl(numpy.ndarray.__ipow__))

class _testCase_power(unittest.TestCase):
    def setUp(self):
        build_eval_mode()
        numpy.random.seed(44)
    def tearDown(self):
        pop_mode()
    def test1(self):
        r = numpy.random.rand(50)
        exp_r = exp(r)
        self.failUnless(exp_r.__array__().__class__ is numpy.ndarray)

    def test_0(self):
        r = numpy.random.rand(50)

        exp_r = exp(r)
        n_exp_r = numpy.exp(r)
        self.failUnless( _approx_eq(exp_r, n_exp_r), 
                (exp_r, exp_r.data, n_exp_r,
                    numpy.max(numpy.abs(n_exp_r.__sub__(exp_r.__array__())))))

        log_exp_r = log(exp_r)
        self.failUnless( _approx_eq(log_exp_r, r), log_exp_r)

    def test_1(self):
        r = numpy.random.rand(50)
        r2 = pow(r,2)
        self.failUnless( _approx_eq(r2, r*r))

## Others ##

class minmax(elemwise):
    nout = 2
    def impl(x):
        return x.min, x.max
    def specs(x):
        return [(numpy.ndarray, x[1], ())] * 2
#     def alloc((x, ), (_min, _max)):
#         _min.data = numpy.ndarray((), x.dtype)
#         _max.data = numpy.ndarray((), x.dtype)
    def c_init((x, ), (_min, _max)):
        raise NotImplementedError
        return """
        _x_dtype min = _x[0];
        _x_dtype max = _x[0];
        """
    def c_foreach((x, ), (_min, _max)):
        return """
        if (x < min) min = x;
        if (x > max) max = x;
        """
    def c_finalize((x, ), (_min, _max)):
        return """
        _min[0] = min;
        _max[0] = max;
        """


class fill(elemwise):
    impl = lambda model, value: (model * 0) + value
    def c_init((model, value), (z, )):
        return "value_dtype value0 = ((value_dtype*)PyArray_DATA(value))[0];"
    def c_foreach((model_i, value), (z_i, )):
        return "z_i = value0;"

fill_inplace = fill.inplace_version()

class sum(elemwise):
    impl = numpy.sum
    def grad(x, gz):
        return fill(x, gz)
    def refresh_shape(self):
        return ()
    def c_init((x, ), (sum, )):
        return "sum_dtype* sump = ((sum_dtype*)PyArray_DATA(sum)); sump[0] = 0;"
    def c_foreach((x_i, ), (sum, )):
        return "sump[0] += x_i;"

class ones_like(elemwise):
    impl = numpy.ones_like
    def grad(x, gz): return Undefined

class zeros_like(elemwise):
    impl = numpy.zeros_like
    def grad(x, gz): return Undefined

## Array slicing ##

class get_slice(omega_op):
    def view_map(self): return {self.out: [self.inputs[0]]}
    def impl(x, item): 
        rval = x.__getitem__(item)
        #print 'get_slice running', rval
        return rval
    def grad(x, gz): raise NotImplemented
    def refresh_shape(self): 
        x,item = self.inputs
        rval = x.data.__getitem__(item.data).shape 
        #print 'refresh_shape', rval
        return rval
    def refresh_dtype(self):
        return self.inputs[0].data.dtype

class _testCase_slicing(unittest.TestCase):
    def setUp(self):
        build_eval_mode()
    def tearDown(self):
        pop_mode()

    def test_getitem0(self):
        a = numpy.ones((4,4))
        wa1 = wrap(a)[:,1]
        try:
            err = wa1 + a
        except ValueError, e:
            self.failUnless(str(e) == \
                    'The dimensions of the inputs do not match.',
                    'Wrong ValueError')
            return
        self.fail('add should not have succeeded')

    def test_getitem1(self):
        a = numpy.ones((4,4))
        wa1 = wrap(a)[1]
        self.failUnless(wa1.data.shape == (4,))

    def test_getslice_0d_all(self):
        """Test getslice does not work on 0d array """
        a = numpy.ones(())
        try:
            wa1 = wrap(a)[:]
        except IndexError, e:
            self.failUnless(str(e) == "0-d arrays can't be indexed.")
            return
        self.fail()
    def test_getslice_1d_all(self):
        """Test getslice on 1d array"""
        a = numpy.ones(4)
        wa1 = wrap(a)[:]
        self.failUnless(wa1.data.shape == (4,), 'wrong shape')
        self.failUnless(numpy.all(wa1.data == a), 'unequal value')

        a[1] = 3.4
        self.failUnless(wa1.data[1] == 3.4, 'not a view')

        try:
            wa1[2] = 2.5
        except TypeError, e:
            self.failUnless("object does not support item assignment" in str(e))
            return
        self.fail()
    def test_getslice_3d_all(self):
        """Test getslice on 3d array"""
        a = numpy.ones((4,5,6))
        wa1 = wrap(a)[:]
        self.failUnless(wa1.data.shape == (4,5,6), 'wrong shape')
        self.failUnless(numpy.all(wa1.data == a), 'unequal value')

        a[1,1,1] = 3.4
        self.failUnless(wa1.data[1,1,1] == 3.4, 'not a view')
    def test_getslice_1d_some(self):
        """Test getslice on 1d array"""
        a = numpy.ones(5)
        wa1 = wrap(a)[1:3]
        a[2] = 5.0
        a[3] = 2.5
        self.failUnless(wa1.data.shape == (2,))
        self.failUnless(a[1] == wa1.data[0])
        self.failUnless(a[2] == wa1.data[1])
    def test_getslice_1d_step(self):
        """Test getslice on 1d array"""
        a = numpy.ones(8)
        wa1 = wrap(a)[0:8:2]
        for i in xrange(8): a[i] = i

        self.failUnless(wa1.shape == (4,))
        for i in xrange(4):
            self.failUnless(a[i*2] == wa1.data[i])
    def test_getslice_3d_float(self):
        """Test getslice on 3d array"""
        a = numpy.asarray(range(4*5*6))
        a.resize((4,5,6))
        wa1 = wrap(a)[1:3]
        self.failUnless(wa1.shape == (2,5,6))
        self.failUnless(numpy.all(a[1:3] == wa1.data))
        a[1] *= -1.0
        self.failUnless(numpy.all(a[1:3] == wa1.data))
    def test_getslice_3d_one(self):
        """Test getslice on 3d array"""
        a = numpy.asarray(range(4*5*6))
        a.resize((4,5,6))
        wa = wrap(a)
        wa_123 = wa[1,2,3]
        self.failUnless(wa_123.shape == (), wa_123.shape)


add = scalar_switch(add_elemwise, add_scalar, add_scalar)
add_inplace = scalar_switch(add_elemwise_inplace, add_scalar_inplace)

sub = scalar_switch(sub_elemwise, sub_scalar_r, sub_scalar_l)
sub_inplace = scalar_switch(sub_elemwise_inplace, sub_scalar_r_inplace)

mul = scalar_switch(mul_elemwise, scale, scale)
mul_inplace = scalar_switch(mul_elemwise_inplace, scale_inplace)

div = scalar_switch(div_elemwise, div_scalar_r, div_scalar_l)
div_inplace = scalar_switch(div_elemwise_inplace, div_scalar_r_inplace)

pow = scalar_switch(pow_elemwise, pow_scalar_r, pow_scalar_l)
pow_inplace = scalar_switch(pow_elemwise_inplace, pow_scalar_r_inplace)


if __name__ == '__main__':
    unittest.main()

