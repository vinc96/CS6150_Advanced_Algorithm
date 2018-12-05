# -*- coding: utf-8 -*-
"""Sketch Based Approximate Nearest Neighbor"""

"""
This class is based on sklearn framework.

Created on Sat Dec  1 19:25:27 2018

@author: sbenw
"""
import numpy as np
from functools import partial

from sklearn.metrics import pairwise_distances_chunked
from sklearn.utils.validation import check_is_fitted
from sklearn.utils import check_array, check_random_state
from sklearn.neighbors.base import NeighborsBase, KNeighborsMixin, UnsupervisedMixin
from sklearn.utils._joblib import effective_n_jobs
from sklearn.decomposition import PCA
import math

class SketchKNN(NeighborsBase, KNeighborsMixin, UnsupervisedMixin):
    """ Sketch Based Approximate Nearest Neighbor.
    
    Based on sklearn framework. Read more in the :
    [https://github.com/scikit-learn/scikit-learn/blob/master/sklearn/neighbors].
    
    Parameters
    ----------
    
    n_neighbors : :obj:`int`, default = 5
        Number of neighbors to use by default for :meth:`kneighbors` queries.
        
    sketch_method : {:obj:`None`, 'symmetric', 'asymmetric', 'g_asymmetric', 'PCA'}, defalut = :obj:`None`
        Method to be used to compute the sketch: 
            
        - None will do all these available sketch, user can choose the method
          when query by :meth:`kneighbors` method.
        - 'symmetric' is not weighted
        - 'asymmetric' is weighted symmetric
        - 'g_asymmetric' is grouped asymmetric
        - 'PCA' use float number in each sketch elements.
        
        Note: If it is not None here, the sketch_method passed to 
        :meth:`kneighbors` method will be ignored.
        
    sketch_size : :obj:`int`, default = 20
        The size of one sketch vector of each data point (row).
        
    strip_window : number, default = 50
        The width of each strip. This parameter does not effect 'PCA' method.
        
    candidates_scale : :obj:`int`, default = 20
        Scale up n_neighbors as number of candidate when filtering using 
        sketch. 
        
    group_size : :obj:`int`, default = 4
        The size of group label vector, only for 'g_asymmetric' method. The 
        label vector use binary bit, so group_size = 3 will result 8 groups.
        
    group_threshold : :obj:`float`, default = 0.1
        The threshold for choose group when query.
        
    random_state : {:obj:`None`, :obj:`int`, :class:`random_state`}, default = :obj:`None`
        Int will be used as numpy random seed.
        
    Examples
    --------
    
    In the following example, we construct a NeighborsClassifier
    class from an array representing our data set and ask who's
    the closest point to [1,1,1]
    
    >>> samples = [[0., 0., 0.], [0., .5, 0.], [1., 1., .5]]
    >>> from sketch import SketchKNN
    >>> neigh = SketchKNN(n_neighbors=1)
    >>> neigh.fit(samples)
    >>> print(neigh.kneighbors([[1., 1., 1.]], return_distance=True))
    (array([[0.5]]), array([[2]]))
    
    As you can see, it returns [[0.5]], and [[2]], which means that the
    element is at distance 0.5 and is the third element of samples
    (indexes start at 0). You can also query for multiple points:
    
    >>> X = [[0., 1., 0.], [1., 0., 1.]]
    >>> neigh.kneighbors(X)
    array([[1],
           [2]]...)
    
    Notes
    -----
    For more details about sketch methods, see paper *Asymmetric Distance 
    Estimation with Sketches for Similarity Search in High-Dimensional Spaces* 
    by Wei Dong, Moses Charikar, and Kai Li.
    """
    def __init__(self, n_neighbors=5, sketch_method=None, sketch_size=20, strip_window = 50, candidates_scale=20,
                group_size=4, group_threshold=0.1, random_state=None,):
        # NeighborsBase
        super(SketchKNN, self).__init__(n_neighbors=n_neighbors)        
        self.sketch_size = sketch_size
        self.strip_window = strip_window
        self.candidates_scale = candidates_scale
        self.random_state = random_state
        self.g_size = group_size
        self.g_strip_window = (sketch_size/group_size)*strip_window
        self.g_threshold = group_threshold        
        
        self.sketch_method = sketch_method
        self._sketch_tech = {'sketch':False, 'weighted':False, 'label':False, 'pca':False}
        if sketch_method is None:
            self._sketch_tech['sketch'] = True
            self._sketch_tech['weighted'] = True
            self._sketch_tech['label'] = True
            self._sketch_tech['pca'] = True
        elif sketch_method == 'symmetric':
            self._sketch_tech['sketch'] = True
        elif sketch_method == 'asymmetric':
            self._sketch_tech['sketch'] = True
            self._sketch_tech['weighted'] = True
        elif sketch_method == 'g_asymmetric':
            self._sketch_tech['sketch'] = True
            self._sketch_tech['weighted'] = True
            self._sketch_tech['label'] = True
        elif sketch_method == 'PCA':
            self._sketch_tech['pca'] = True
        else:
            raise ValueError("%s sketch_method has not been implemented.".format(self.sketch_method))
    
    def fit(self, X):
        """Fit the model using X as data
        
        Parameters
        ----------
        X : array-like, shape [n_samples, n_features]
            Data, from where find the query point's neighbors.
        """
        super(SketchKNN, self).fit(X) # generate self._fit_X
        
        if self._sketch_tech['sketch']:
            self._partition()
            if self._sketch_tech['label']:
                self._sketch_X, self._g_sketch_X = self._sketch(self._fit_X, return_label=True)
            else:
                self._sketch_X = self._sketch(self._fit_X)
        if self._sketch_tech['pca']:
            self._pca = PCA(n_components=self.sketch_size)
            self._pca_X = self._pca.fit_transform(self._fit_X)

        return self
    
    def _partition(self):
        random_state = check_random_state(self.random_state)
        n_features = self._fit_X.shape[1]
        self._A = random_state.normal(size = [self.sketch_size, n_features]) / math.sqrt(n_features)
        self._b = random_state.uniform(0, self.strip_window, self.sketch_size)
        self._g_A = random_state.normal(size = [self.g_size, n_features]) / math.sqrt(n_features)
        self._g_b = random_state.uniform(0, self.g_strip_window, self.g_size)

    def kneighbors(self, X, n_neighbors=None, sketch_method=None, candidates_scale=None, return_distance=False):
        """Fast finds the approximate K-neighbors of each point using sketch.
        Returns indices of and distances to the neighbors of each point.
        
        Parameters
        ----------
        X : array-like, shape (n_query, n_features).
            The query point or points.
        n_neighbors : :obj:`int`, :obj:
            Number of neighbors to get.
        sketch_method : {:obj:`None`, 'symmetric', 'asymmetric', 'g_asymmetric', 'PCA'}, defalut = :obj:`None`
            Method to be used to filter candidates before rank the real distances.
            If non None value passed to the constructor, this value will be 
            ignored. If both constructor and this method get None, It will not
            use any sketch filter, act just like normal KNN. See constructor 
            for more details.
        candidates_scale : :obj:`int`, default is the value passed to the constructor
            Scale up n_neighbors as number of candidate when filtering using 
            sketch. 
        return_distance : :obj:`boolean`, default = :obj:`False`.
            If False, distances will not be returned
        
        Returns
        -------
        dist : :obj:`array`
            Array representing the lengths to points, only present if
            return_distance= :obj:`True`
        ind : :obj:`array`
            Indices of the nearest points in the population matrix.
        """
        check_is_fitted(self, ["_fit_X"])
        
        if n_neighbors is None:
            n_neighbors = self.n_neighbors
        
        X = check_array(X)
        
        if self.sketch_method is not None:
            sketch_method = self.sketch_method
            
        # reduce_func for neighbors
        reduce_func_k = partial(self._kneighbors_reduce_func,
                                  n_neighbors=n_neighbors,
                                  return_distance=return_distance)
        kwds = ({'squared': True})
        n_jobs = effective_n_jobs(self.n_jobs)
        
        # result to return
        if return_distance:
            dists = []
        neight_inds = []
        
        # find candidates
        if sketch_method is None: # KNN
            pass
        else:
            if candidates_scale is None:
                candidates_scale = self.candidates_scale
            n_candidates = self.n_neighbors * candidates_scale
            reduce_func_1 = partial(self._kneighbors_reduce_func,
                                      n_neighbors=n_candidates,
                                      return_distance=False)
            if sketch_method == 'symmetric':
                sketch_X = self._sketch(X)
                candidates = list(pairwise_distances_chunked(
                        sketch_X, self._sketch_X, reduce_func=reduce_func_1,
                        metric=paired_hamming_distance, n_jobs=n_jobs))
            elif sketch_method == 'asymmetric':
                # TODO: sketch X (query points)
                sketch_X, weight = self._sketch(X, return_weight=True)
                _sketch_X_weight = sketch_X+weight # encode sketch_X and weight together
                # TODO: filter candidates
                candidates = list(pairwise_distances_chunked(
                        _sketch_X_weight, self._sketch_X, reduce_func=reduce_func_1,
                        metric=paired_asymmetric_distance, n_jobs=n_jobs))
            elif sketch_method == 'PCA':
                # sketch X (query points)
                sketch_X = self._pca.transform(X)
                # filter candidates
                candidates = list(pairwise_distances_chunked(
                        sketch_X, self._pca_X, reduce_func=reduce_func_1,
                        metric=self.effective_metric_, n_jobs=n_jobs, **kwds))
            elif sketch_method == 'g_asymmetric':
                # TODO: sketch X (query points)
                sketch_X, weight, g_sketch_X, g_weight = self._sketch(X, return_weight=True, return_label=True)
                _sketch_X_weight = sketch_X+weight # encode sketch_X and weight together
                # TODO: filter label
                Inds = [np.where(q>=self.g_threshold)[0] for q in g_weight] # each row: indices of (q>=threshold)
                Label = [q[inds] for q, inds in zip(g_sketch_X, Inds)] # each row: binary elms of (q>=threshold)
                Candidate_inds = [np.where((self._g_sketch_X[:,inds]==label).all(axis=1))[0]
                                  for label, inds in zip(Label, Inds)] # each row: self._g_sketch_X's row numbers that matched label
                # TODO: filter candidates
                candidates = []
                for i in range(len(Candidate_inds)):
                    candidate_inds = Candidate_inds[i]
                    tmp1 = self._sketch_X[candidate_inds, :]
                    tmp2 = _sketch_X_weight[[i]]
                    candidates += list(pairwise_distances_chunked(
                            tmp2, tmp1, reduce_func=reduce_func_1,
                            metric=paired_asymmetric_distance, n_jobs=n_jobs))
            else:
                raise ValueError("%s sketch_method has not been implemented.".format(sketch_method))
            candidates = np.vstack(candidates)
        
        # find neighbors
        if sketch_method is None: # KNN
            # find neighbors from all data points
            result = list(pairwise_distances_chunked(
                X, self._fit_X, reduce_func=reduce_func_k,
                metric=self.effective_metric_, n_jobs=n_jobs,
                **kwds))
            if return_distance:
                dist, neigh_ind = zip(*result)
                result = np.vstack(dist), np.vstack(neigh_ind)
            else:
                result = np.vstack(result)
        else:
            # find neighbors from the candidate points.
            for i in range(len(candidates)):
                result = list(pairwise_distances_chunked(
                        X[[i],:], self._fit_X[candidates[i]], reduce_func=reduce_func_k,
                        metric=self.effective_metric_, n_jobs=n_jobs,
                        **kwds))
                if return_distance:
                    dist, neigh_ind = zip(*result)
                    dists.append(dist[0][0])
                    neigh_ind = np.hstack(neigh_ind).reshape(-1)
                    neight_inds.append(candidates[i][neigh_ind])
                else:
                    neight_inds.append(candidates[i][np.vstack(result)[0]])
            if return_distance:
                result = dists, neight_inds
            else:
                result = neight_inds
        
        return result
    
    def _sketch(self, X, return_weight = False, return_label = False):
        # self._fit_X, self._A, self._W, self._b
        result = ()

        h = (X.dot(self._A.T) + self._b) / self.strip_window
        result += np.mod(np.floor(h),2), # sketch
        if return_weight:
             result += np.minimum(np.ceil(h) - h, h - np.floor(h)), # weight
        if return_label:
            g_h = (X.dot(self._g_A.T) + self._g_b) / self.g_strip_window
            result += np.mod(np.floor(g_h), 2), # label
            if return_weight:
                result += np.minimum(np.ceil(g_h) - g_h, g_h - np.floor(g_h)), # g_weight

        return result[0] if len(result) == 1 else result
    
        
# Utility Functions
def paired_hamming_distance(x, y):
    return np.count_nonzero(x - y)

def paired_asymmetric_distance(x, y):
    sketch_X = np.floor(x)
    weight = x % 1
    return weight.dot(np.abs(sketch_X - y).T)

if __name__ == '__main__':
    data = np.load("..\data\Caltech101_small.npy")
    neigh = SketchKNN(n_neighbors=5, sketch_size = 20, random_state = 0)
    neigh.fit(data)
    dists, neight_inds = neigh.kneighbors(data[:2,:], sketch_method = 'PCA', return_distance=True, candidates_scale = 20)
    print("distance: ", dists)
    print("neight_inds: ", neight_inds)
    '''
    samples = [[0., 0., 0.], [0., 500., 0.], [100., 100., 50.]]
    neigh = SketchKNN(n_neighbors=2, sketch_size = 3, random_state = 0)
    neigh.fit(samples)
    dists, neight_inds = neigh.kneighbors([[100., 100., 100.],[100., 500., 100.]], return_distance=True, candidates_scale = 1)
    print("distance: ", dists)
    print("neight_inds: ", neight_inds)
    '''
    '''
    samples = [[0., 0., 0.], [0., .5, 0.], [1., 1., .5]]
    neigh = SketchKNN(n_neighbors=1)
    neigh.fit(samples)
    print(neigh.kneighbors([[1., 1., 1.]], return_distance=True))
    '''