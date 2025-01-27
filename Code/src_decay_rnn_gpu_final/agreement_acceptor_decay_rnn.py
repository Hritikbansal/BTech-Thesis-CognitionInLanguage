import warnings
warnings.filterwarnings("ignore", message="numpy.dtype size changed")
warnings.filterwarnings("ignore", message="numpy.ufunc size changed")

import torch
import torch.nn as nn
import numpy as np
import pandas as pd
import random
import six
import pickle
from decay_rnn_model import DECAY_RNN_Model, BatchedDataset
from torch.utils.data import Dataset
from torch.utils.data import DataLoader
from decay_rnn import LSTM
from utils import gen_inflect_from_vocab, dependency_fields, dump_dict_to_csv

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print(device)


def pad_sequences(sequences, maxlen=None, dtype='int32',
                  padding='pre', truncating='pre', value=0.):
    if not hasattr(sequences, '__len__'):
        raise ValueError('`sequences` must be iterable.')
    lengths = []
    for x in sequences:
        if not hasattr(x, '__len__'):
            raise ValueError('`sequences` must be a list of iterables. '
                             'Found non-iterable: ' + str(x))
        lengths.append(len(x))

    num_samples = len(sequences)
    if maxlen is None:
        maxlen = np.max(lengths)

    # take the sample shape from the first non empty sequence
    # checking for consistency in the main loop below.
    sample_shape = tuple()
    for s in sequences:
        if len(s) > 0:
            sample_shape = np.asarray(s).shape[1:]
            break

    is_dtype_str = np.issubdtype(dtype, np.str_) or np.issubdtype(dtype, np.unicode_)
    if isinstance(value, six.string_types) and dtype != object and not is_dtype_str:
        raise ValueError("`dtype` {} is not compatible with `value`'s type: {}\n"
                         "You should set `dtype=object` for variable length strings."
                         .format(dtype, type(value)))

    x = np.full((num_samples, maxlen) + sample_shape, value, dtype=dtype)
    for idx, s in enumerate(sequences):
        if not len(s):
            continue  # empty list/array was found
        if truncating == 'pre':
            trunc = s[-maxlen:]
        elif truncating == 'post':
            trunc = s[:maxlen]
        else:
            raise ValueError('Truncating type "%s" '
                             'not understood' % truncating)

        # check `trunc` has expected shape
        trunc = np.asarray(trunc, dtype=dtype)
        if trunc.shape[1:] != sample_shape:
            raise ValueError('Shape of sample %s of sequence at position %s '
                             'is different from expected shape %s' %
                             (trunc.shape[1:], idx, sample_shape))

        if padding == 'post':
            x[idx, :len(trunc)] = trunc
        elif padding == 'pre':
            x[idx, -len(trunc):] = trunc
        else:
            raise ValueError('Padding type "%s" not understood' % padding)
    return x

class RNNAcceptor(DECAY_RNN_Model):


    def update_dump_dict(self, key,x_test_minibatch, y_test_minibatch, predicted):

        x =  x_test_minibatch.tolist()
        y =  y_test_minibatch.tolist()
        p =  predicted
        for i in range(len(x)):
            string =  self.input_to_string(x[i])
            self.dump_dict[key].append((string, y[i], p))
        string =  self.input_to_string(x)
        self.dump_dict[key].append((string, y, p))

    def test_model(self):
        # we will do this in batched fashion
        print("Entered testing phase")
        result_dict = {}
        self.dump_dict = {}
        if not hasattr(self,"testing_dict"):
            self.demark_testing()

        with torch.no_grad():
            for keys in (self.testing_dict.keys()):
                x_ , y_ = zip(*self.testing_dict[keys])
                x_ = np.asarray(list(x_))
                y_ = np.asarray(list(y_))
                batch_generator = BatchedDataset(x_, y_)
                DataGenerator = DataLoader(batch_generator, batch_size=self.batch_size, drop_last=False) # we should not drop the last ones, its bad
                self.dump_dict[keys]=[]
                accuracy=0
                total_example=0
                for x_test, y_test in DataGenerator:
                    x_test = torch.tensor(x_test, dtype=torch.long).to(device)
                    y_test = torch.tensor(y_test, dtype=torch.long).to(device)
                    m = x_test.shape[0] 
                    total_example += m
                    assert m==y_test.shape[0] 
                    x_test = x_test.view(m, -1)
                    y_test = y_test.view(m,) 
                    pred, _, _ = self.model(x_test)
                    y_hat = torch.argmax(pred, dim =1)
                    y_hat = y_hat.view(m,)
                    accuracy+= torch.sum(y_hat == y_test).item()
                    self.update_dump_dict(keys, x_test, y_test, predicted)

                result_dict[keys] = (accuracy/total_example, total_example)

        dump_dict_to_csv(self.dump_dict)
        self.log(str(result_dict))
        return result_dict


    def create_train_and_test(self, examples, test_size, data_name, save_data=False):
        d = [[], []]
        for i, s, dep in examples:
            d[i].append((i, s, dep))
        random.seed(1)
        random.shuffle(d[0])
        random.shuffle(d[1])
        if self.equalize_classes:
            l = min(len(d[0]), len(d[1]))
            examples = d[0][:l] + d[1][:l]
        else:
            examples = d[0] + d[1]
        random.shuffle(examples)

        Y, X, deps = zip(*examples)
        Y = np.asarray(Y)
        X = pad_sequences(X, maxlen = self.maxlen)

        n_train = int(self.prop_train * len(X))
        # self.log('ntrain', n_train, self.prop_train, len(X), self.prop_train * len(X))
        self.X_train, self.Y_train = X[:n_train], Y[:n_train]
        self.deps_train = deps[:n_train]
        if (test_size > 0) :
            self.X_test, self.Y_test = X[n_train : n_train+test_size], Y[n_train : n_train+test_size]
            self.deps_test = deps[n_train : n_train+test_size]
        else :
            self.X_test, self.Y_test = X[n_train:], Y[n_train:]
            self.deps_test = deps[n_train:]

        if (save_data) :
            with open('X_' + data_name + '_data.pkl', 'wb') as f:
                pickle.dump(X, f)
            with open('Y_' + data_name + '_data.pkl', 'wb') as f:
                pickle.dump(Y, f)
            with open('deps_' + data_name + '_data.pkl', 'wb') as f:
                pickle.dump(deps, f)

    def load_train_and_test(self, test_size, data_name):
        # Y = np.asarray(Y)
        # X = pad_sequences(X, maxlen = self.maxlen)

        with open('../grammar_data/' + data_name + '_v2i.pkl', 'rb') as f:
            self.vocab_to_ints = pickle.load(f)

        with open('../grammar_data/' + data_name + '_i2v.pkl', 'rb') as f:
            self.ints_to_vocab = pickle.load(f)
        X = []
        Y = []

        with open('../grammar_data/X_' + data_name + '_data.pkl', 'rb') as f:
            X = pickle.load(f)

        with open('../grammar_data/Y_' + data_name + '_data.pkl', 'rb') as f:
            Y = pickle.load(f)

        with open('../grammar_data/deps_' + data_name + '_data.pkl', 'rb') as f:
            deps = pickle.load(f)

        n_train = int(self.prop_train * len(X))
        self.X_train, self.Y_train = X[:n_train], Y[:n_train]
        self.deps_train = deps[:n_train]

        if (test_size > 0) :
            self.X_test, self.Y_test = X[n_train : n_train+test_size], Y[n_train : n_train+test_size]
            self.deps_test = deps[n_train : n_train+test_size]
        else :
            self.X_test, self.Y_test = X[n_train:], Y[n_train:]
            self.deps_test = deps[n_train:]


    def create_model_batched(self):
        self.log('Creating Batched model')
        self.log('vocab size : ' + str(len(self.vocab_to_ints)))
        self.model = LSTM(input_units = self.maxlen ,hidden_units = self.hidden_dim, vocab_size = len(self.vocab_to_ints)+1, batch_size=self.batch_size,embedding_dim=self.embedding_size).to(device)


    def results_batched(self):
        self.log('Processing cross validataion set')
        predicted = []
        testing_batches = len(self.X_test)//self.testing_batch_size
        x_test = np.asarray(self.X_test)
        y_test = np.asarray(self.Y_test)
        test_batch_dataset = BatchedDataset(x_test, y_test)
        DataGenerator =  DataLoader(test_batch_dataset, batch_size= self.testing_batch_size, shuffle=False, num_workers=0, drop_last=False)        
        tot_testing_ex = 0
        acc = 0
        for x_, y_ in DataGenerator:
            m = x_.shape[0]
            tot_testing_ex+=m
            assert m==y_.shape[0]
            x_  = x_.view(m, -1).to(device)
            y_  = y_.view(m,).to(device)
            out, _, _ = self.model(x_)
            y_hat = torch.argmax(out, dim=1)
            y_hat = y_hat.view(m, )
            acc += torch.sum(y_hat == y_).item()
        self.log("Accuracy on testing set is {}".format(acc/tot_testing_ex))

        return acc

    # def create_model(self):
    #     self.log('Creating model')
    #     self.log('vocab size : ' + str(len(self.vocab_to_ints)))
    #     self.model = LSTM(input_units = self.maxlen ,hidden_units = self.hidden_dim, vocab_size = len(self.vocab_to_ints)+1, embedding_dim=self.embedding_size).to(device)


    # def results(self):
    #     self.log('Processing test set')
    #     predicted = []
    #     x_test = torch.tensor(self.X_test, dtype=torch.long).to(device)
    #     # x_test = self.X_test
    #     self.log(str(len(self.X_train)) + ', ' + str(len(x_test)))

    #     with torch.no_grad():
    #         for index in range(len(x_test)) :
    #             pred, hidden, output = self.model(x_test[index])
    #             if (pred[0][0] > pred[0][1]) :
    #                 predicted.append([0])
    #             else :
    #                 predicted.append([1])
    #         recs = []
    #         columns = ['correct', 'prediction', 'label'] + dependency_fields
    #         for dep, prediction in zip(self.deps_test, predicted):
    #             prediction = self.code_to_class[prediction[0]]
    #             recs.append((prediction == dep['label'], prediction, dep['label']) + tuple(dep[x] for x in dependency_fields))
        
    #     self.test_results = pd.DataFrame(recs, columns=columns)
    #     xxx = self.test_results['correct']
    #     self.log('Accuracy : ' + str(sum(xxx)))
    #     return sum(xxx)

    # def validate_training(self, batch_list):
    #     # This function will evaluate the training accuracy for the batches so far. 
    #     validation_size=len(batch_list) 
    #     accurate = 0
    #     total = 0
    #     self.log("Started Training data validataion")
    #     self.log("Validating on {} batches of training data".format(validataion_size))
    #     total_validation_done = 0
    #     with torch.no_grad():
    #         for x_val, y_val in batch_list:
    #             pred, hidden, output = self.model(x_val)
    #             for i in range(pred.shape[0]):
    #                 total+=1
    #                 if pred[i][0]>pred[i][1]:
    #                     if y_val[i].item()==0 :
    #                         accurate=accurate+1
    #                 if pred[i][0]<pred[i][1]:
    #                     if y_val[i].item()==1 :
    #                         accurate=accurate+1

    #     self.log("Total accurate : {}/{}".format(accurate, total))
    #     print("Total accurate : {}/{}".format(accurate, total))

    # def result_demarcated(self):
    #     if not hasattr(self, "testing_dict"):
    #         self.demark_testing()

    #     result_dict={}
    #     with torch.no_grad():
    #         for key in self.testing_dict.keys():
    #             predicted=[]
    #             accuracy=0
    #             tot=0
    #             for x_test, y_test in self.testing_dict[key]:
    #                 tot += 1
    #                 y_test = np.asarray(y_test)
    #                 x_test = torch.tensor(x_test, dtype=torch.long).to(device)
    #                 T=x_test.view(1,len(x_test))
    #                 pred, _, _ = self.model(T)
    #                 if (pred[0][0] > pred[0][1]) :
    #                     predicted=0
    #                 else :
    #                     predicted=1

    #                 if(predicted==(y_test)) :
    #                     accuracy+=1
    #             result_dict[key] = (accuracy/tot , tot)
    #     self.log(str(result_dict))
    #     return result_dict


    # def results_verbose(self, df_name='_verbose_.pkl'):
    #     self.log('Processing test set')
    #     predicted, all_hidden, all_output = [], [], []
    #     x_test = torch.tensor(self.X_test, dtype=torch.long).to(device)
    #     self.log(str(len(self.X_train)) + ', ' + str(len(x_test)))

    #     with torch.no_grad():
    #         for index in range(len(x_test)) :
    #             if (index % 1000 == 0):
    #                 self.log(index)
    #             pred, hidden, output = self.model(x_test[index])
    #             # all_hidden.append(hidden)
    #             # all_output.append(output)
    #             if (pred[0][0] > pred[0][1]) :
    #                 predicted.append([0])
    #             else :
    #                 predicted.append([1])
    #         recs = []
    #         columns = ['correct', 'prediction', 'label'] + dependency_fields
    #         for dep, prediction in zip(self.deps_test, predicted):
    #             prediction = self.code_to_class[prediction[0]]
    #             recs.append((prediction == dep['label'], prediction, dep['label']) + tuple(dep[x] for x in dependency_fields))
        
    #     self.test_results = pd.DataFrame(recs, columns=columns)
    #     self.test_results.to_pickle(df_name)
    #     # self.test_results['activations'] = all_hidden
    #     # self.test_results['outputs'] = all_output
    #     # self.test_results.to_pickle(df_name)
    #     xxx = self.test_results['correct']
    #     self.log('Accuracy : ' + str(sum(xxx)))
    #     return sum(xxx)

    # def results_train(self):
    #     self.log('Processing train set')
    #     predicted = []
    #     x_train = torch.tensor(self.X_train, dtype=torch.long).to(device)
    #     self.log(len(x_train))
    #     with torch.no_grad():
    #         for index in range(len(x_train)) :
    #             pred = self.model(x_train[index])
    #             if (pred[0][0] > pred[0][1]) :
    #                 predicted.append([0])
    #             else :
    #                 predicted.append([1])
    #         recs = []
    #         columns = ['correct', 'prediction', 'label'] + dependency_fields
    #         for dep, prediction in zip(self.deps_train, predicted):
    #             prediction = self.code_to_class[prediction[0]]
    #             recs.append((prediction == dep['label'], prediction, dep['label']) + tuple(dep[x] for x in dependency_fields))
        
    #     self.test_results = pd.DataFrame(recs, columns=columns)
    #     xxx = self.test_results['correct']
    #     self.log('Accuracy : ' + str(sum(xxx)))
    #     return sum(xxx)

class PredictVerbNumber(RNNAcceptor):

    def __init__(self, *args, **kwargs):
        RNNAcceptor.__init__(self, *args, **kwargs)
        self.class_to_code = {'VBZ': 0, 'VBP': 1}
        self.code_to_class = {x: y for y, x in self.class_to_code.items()}

    def process_single_dependency(self, dep):
        dep['label'] = dep['verb_pos']
        v = int(dep['verb_index']) - 1
        tokens = dep['sentence'].split()[:v]
        return tokens

class InflectVerb(PredictVerbNumber):
    '''
    Present all words up to _and including_ the verb, but withhold the number
    of the verb (always present it in the singular form). Supervision is
    still the original number of the verb. This task allows the system to use
    the semantics of the verb to establish the dependency with its subject, so
    may be easier. Conversely, this may mess up the embedding of the singular
    form of the verb; one solution could be to expand the vocabulary with
    number-neutral lemma forms.
    '''

    def __init__(self, *args, **kwargs):
        super(InflectVerb, self).__init__(*args, **kwargs)
        self.inflect_verb, _ = gen_inflect_from_vocab(self.vocab_file)

    def process_single_dependency(self, dep):
        dep['label'] = dep['verb_pos']
        v = int(dep['verb_index']) - 1
        tokens = dep['sentence'].split()[:v+1]
        if dep['verb_pos'] == 'VBP':
            tokens[v] = self.inflect_verb[tokens[v]]
        return tokens

class CorruptAgreement(RNNAcceptor):

    def __init__(self, *args, **kwargs):
        RNNAcceptor.__init__(self, *args, **kwargs)
        self.class_to_code = {'grammatical': 0, 'ungrammatical': 1}
        self.code_to_class = {x: y for y, x in self.class_to_code.items()}
        self.inflect_verb, _ = gen_inflect_from_vocab(self.vocab_file)

    def process_single_dependency(self, dep):
        tokens = dep['sentence'].split()
        if random.random() < 0.5:
            dep['label'] = 'ungrammatical'
            v = int(dep['verb_index']) - 1
            tokens[v] = self.inflect_verb[tokens[v]]
            dep['sentence'] = ' '.join(tokens)
        else:
            dep['label'] = 'grammatical'
        return tokens


class GrammaticalHalfSentence(RNNAcceptor):

    def __init__(self, *args, **kwargs):
        RNNAcceptor.__init__(self, *args, **kwargs)
        self.class_to_code = {'grammatical': 0, 'ungrammatical': 1}
        self.code_to_class = {x: y for y, x in self.class_to_code.items()}
        self.inflect_verb, _ = gen_inflect_from_vocab(self.vocab_file)

    def process_single_dependency(self, dep):
        tokens = dep['sentence'].split()
        v = int(dep['verb_index']) - 1
        tokens = tokens[:v+1]
        if random.random() < 0.5:
            dep['label'] = 'ungrammatical'
            tokens[v] = self.inflect_verb[tokens[v]]
        else:
            dep['label'] = 'grammatical'
        dep['sentence'] = ' '.join(tokens[:v+1])
        return tokens

class GramHalfPlusSentence(RNNAcceptor):

    def __init__(self, *args, **kwargs):
        RNNAcceptor.__init__(self, *args, **kwargs)
        self.class_to_code = {'grammatical': 0, 'ungrammatical': 1}
        self.code_to_class = {x: y for y, x in self.class_to_code.items()}
        self.inflect_verb, _ = gen_inflect_from_vocab(self.vocab_file)

    def process_single_dependency(self, dep):
        tokens = dep['sentence'].split()
        v = int(dep['verb_index']) - 1
        tokens = tokens[:v+1 + self.len_after_verb]
        if random.random() < 0.5:
            dep['label'] = 'ungrammatical'
            tokens[v] = self.inflect_verb[tokens[v]]
        else:
            dep['label'] = 'grammatical'
        dep['sentence'] = ' '.join(tokens[:v+1 + self.len_after_verb])
        return tokens

class FullGramSentence(RNNAcceptor):

    def __init__(self, *args, **kwargs):
        RNNAcceptor.__init__(self, *args, **kwargs)
        self.class_to_code = {'grammatical': 0, 'ungrammatical': 1}
        self.code_to_class = {x: y for y, x in self.class_to_code.items()}
        self.inflect_verb, _ = gen_inflect_from_vocab(self.vocab_file)

    def process_single_dependency(self, dep):
        tokens = dep['sentence'].split()
        v = int(dep['verb_index']) - 1
        #tokens = tokens[:v+1 + self.len_after_verb]
        if random.random() < 0.5:
            dep['label'] = 'ungrammatical'
            tokens[v] = self.inflect_verb[tokens[v]]
        else:
            dep['label'] = 'grammatical'
        return tokens
