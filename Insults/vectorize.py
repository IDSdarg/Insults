"""
Code for the insults competition run by Kaggle in August 2012.


Usage
-----

Ideas
-----

- use character n-grams because they are robust and simple.
- tune SGD carefully.



"""

import pandas
from sklearn import feature_extraction,linear_model,cross_validation,pipeline,svm
import ml_metrics
import numpy as np
import os
import itertools
import logging



def initialize():
	"""
	Set everything up.
	"""
	logging.basicConfig(filename="vectorize.log",mode='w',format='%(asctime)s : %(levelname)s : %(message)s', level=logging.INFO)
	train = pandas.read_table('Data/train.csv',sep=',')
	leaderboard = pandas.read_table('Data/test.csv',sep=',')
	return train,leaderboard

def scale_predictions(ypred):
	"""
	normalize range of predictions to 0-1
	"""

	yrange = ypred.max() - ypred.min()
	ypred -= ypred.min()
	ypred /= yrange
	
	# protection against rounding, ENSURE nothing out of range.
	ypred[ypred > 1] = 1
	ypred[ypred < 0] = 0
	return ypred
	
	
class MySGDRegressor(linear_model.SGDRegressor):
	"""
	An SGD regressor that 
	a) transforms the output into 0..1
	b) fits in stages so you can see the effect of number of iterations.
	"""
	def __init__(self,n_iter_per_step=50,max_iter=500,alpha=0.001,penalty='l2',**kwargs):
		self.max_iter=max_iter
		self.kwargs = kwargs
		self.n_iter_per_step = n_iter_per_step
		self.alpha = alpha
		self.penalty = penalty
		self.reset_args()
	def reset_args(self):
		# enforce condition that n_iter_per_step must be a factor of n_iter
		assert self.max_iter % self.n_iter_per_step == 0
		linear_model.SGDRegressor.__init__(self,
											alpha=self.alpha,
											learning_rate='constant',
											eta0=1e-3,
											penalty=self.penalty,
											n_iter=self.n_iter_per_step,
											**self.kwargs)
	def fit(self,X,y):
		self.coef_ = None
		self.intercept_ = None
		self.stages_ = []
		for i in range(0,self.max_iter,self.n_iter):
			
			if self.coef_ != None:
				assert(self.intercept_ != None)
				linear_model.SGDRegressor.fit(self,X,y,coef_init=self.coef_,intercept_init=self.intercept_)
			else:
				linear_model.SGDRegressor.fit(self,X,y)
			# record coefs and intercept for later
			self.stages_.append((i+self.n_iter,self.coef_.copy(),self.intercept_.copy()))
			logging.info('done %d/%d steps' % (i+self.n_iter,self.max_iter))
			logging.info('training set auc %f' % self.auc(X,y))
	def auc(self,X,y):
		yhat = self.predict(X)
		return ml_metrics.auc(np.array(y),yhat)

	def staged_predict(self,X):
		"""
		Predict after each of the stages.
		"""
		return [(n_iter_,self.predict(X,coef=coef_,intercept=intercept_)) for (n_iter_,coef_,intercept_) in self.stages_]

	def staged_auc(self,X,y):
		"""
		calculate the AUC after each of the stages.

		returns: ns   -- list of iteration numbers
		         aucs -- list of corresponding areas under the curve.
		"""
		y = np.array(y)
		results = [ (n, ml_metrics.auc(y,p)) for n,p in self.staged_predict(X)]

		return zip(*results) # Python idiom unzips list into two parallel ones.
		
	def predict(self,X,coef=None,intercept=None):
		"""
		a) do the prediction based on given coefs and intercept, if provided.
		b) Scale the predictions so that they are in 0..1. 

		"""
		if coef != None:
			assert intercept != None
			self.intercept_ = intercept
			self.coef_ = coef

		return scale_predictions(linear_model.SGDRegressor.predict(self,X))




class MyCountVectorizer(feature_extraction.text.CountVectorizer):
 def _char_ngrams(self, text_document):
        """Tokenize text_document into a sequence of character n-grams"""
        # normalize white spaces
        text_document = self._white_spaces.sub(u" ", text_document)

        text_len = len(text_document)
        ngrams = []
        min_n, max_n = self.ngram_range
        for n in xrange(min_n, min(max_n + 1, text_len + 1)):
            for i in xrange(text_len - n + 1):
            	ngram = text_document[i: i + n]
                ngrams.append(ngram)

        return ngrams












class MyPipeline(pipeline.Pipeline):
	def staged_auc(self,X,y):
		"""
		MyPipeline knows about staged_auc, which 
		MySGDRegressor implements and uses.
		"""
		Xt = X
		for name, transform in self.steps[:-1]:
			Xt = transform.transform(Xt)
		return self.steps[-1][-1].staged_auc(Xt,y)






clf = MyPipeline([
		    		('vect', feature_extraction.text.CountVectorizer(
		    				lowercase=False,
		    				analyzer='char',
		    				ngram_range=(1,5),
		    				)
		    		),
		    		('tfidf', feature_extraction.text.TfidfTransformer(sublinear_tf=True,norm='l2')),
		    		# first SGD is for sparsity, will be tuned with alpha as large as possible...
		    		('filter',linear_model.SGDRegressor(alpha=1e-5,penalty='l1',n_iter=200)),
		    		# second SGD is for feature weighting...
					("clf",MySGDRegressor(alpha=4e-7, penalty='l1', max_iter=1600, n_iter_per_step=10))
					])



def save_fold_info(i,xs,ys):
	df = pandas.DataFrame({'iterations':xs,
							('auc%d' % i):ys})
	df.to_csv('Folds/fold%d.csv' %i)

def collect_fold_info():
	df = None
	for fn in os.listdir('Folds'):
		fold = pandas.read_table(os.path.join('Folds',fn),sep=',')
		if df == None:
			df = fold
		else:
			df = df.join(fold,on='iterations')
	return df

def training():
	"""
	Train the model, while holding out folds for use in
	estimating performance.

	"""
	best_iters = []
	best_iter = 0 # clf.steps[-1][-1].max_iter / 	clf.steps[-1][-1].n_iter_per_step	
	kf = cross_validation.KFold(len(train.Insult),15,indices=False)
	for i,(train_i,test_i) in enumerate(kf):
		ftrain = train[train_i]
		logging.info('fold %d' % i)
		clf.fit(ftrain.Comment,ftrain.Insult)
		ypred = clf.predict(ftrain.Comment) 
		logging.info("%d train auc=%f" % (i, ml_metrics.auc(np.array(ftrain.Insult),ypred)))
		ypred = clf.predict(train[test_i].Comment)

		# the policy is that we are going to select the number of iterations for the
		# predict stage by examining the curve for the training folds.

		xs,ys = clf.staged_auc(train[test_i].Comment,train[test_i].Insult)
		xs = np.array(xs)
		ys = np.array(ys)
		
		save_fold_info(i,xs,ys)
		logging.info("saved info for fold %d" % i)
		

	best_iters = sorted(best_iters)
	clf.steps[-1][-1].max_iter =  best_iters[len(best_iters) / 2]
	logging.info('training complete')
	print collect_fold_info()


def predict():
	logging.info("Starting leaderboard")
	clf.fit(train.Comment,train.Insult)
	ypred = clf.predict(leaderboard.Comment)

	# we create a submission...
	submission = pandas.read_table('Data/sample_submission_null.csv',sep=',')
	submission['Insult'] = ypred
	for x in itertools.count(1):
			filename = "submissions/submission%d.csv" % x
			if os.path.exists(filename):
				next
			else:			
				submission.to_csv(filename,index=False)
				logging.info('Saved %s' % filename)
				break

if __name__ == "__main__":
	train,leaderboard = initialize()
	training()
	predict()

