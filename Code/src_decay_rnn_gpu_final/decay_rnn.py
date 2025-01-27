import torch
import torch.nn as nn
import torch.optim as optim
from torch.autograd import Variable
import numpy as np
import math
from numpy.random import binomial
import sys

_VF = torch._C._VariableFunctions

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")



def rectify(x):
    relu = nn.ReLU()
    return relu(x)


def give_bin(p):
    a = binomial(n=1, p=p)
    if (a==0):
        return 1
    else:
        return -1

class LstmModule(nn.Module):


    def __init__(self, input_units, output_units, hidden_units, batch_size=1, bias = True, num_chunks = 1, embedding_dim = 200):
        super(LstmModule, self).__init__()

        input_size = input_units
        hidden_size = hidden_units

        self.sigmoid = nn.Sigmoid()
        self.tanh = nn.Tanh()
        self.relu = nn.ReLU()
        self.input_size = input_size
        self.hidden_size = hidden_size
        self.batch_size = batch_size
        self.bias = bias
        self.num_chunks = num_chunks
        self.rgate = nn.Parameter(torch.tensor(0.8).to(device))
        self.embedding_dim=embedding_dim
        #self.weight_ih = nn.Parameter(torch.Tensor(embedding_dim, num_chunks*hidden_size).to(device))
        self.weight_ih = nn.Parameter(torch.Tensor(num_chunks*hidden_size,embedding_dim).to(device))
        self.weight_hh = nn.Parameter(torch.Tensor(num_chunks * hidden_size, hidden_size).to(device))
        self.d_rec = nn.Parameter(torch.zeros(num_chunks * hidden_size, hidden_size).to(device),requires_grad=False)
        
        if bias:
            self.bias_ih = nn.Parameter(torch.Tensor(num_chunks * hidden_size).to(device))
            self.bias_hh = nn.Parameter(torch.Tensor(num_chunks * hidden_size).to(device))
        else:
            self.register_parameter('bias_ih', None)
            self.register_parameter('bias_hh', None)
        
        self.reset_parameters()

    def reset_parameters(self):
        stdv = 1.0 / math.sqrt(self.hidden_size)
        for weight in self.parameters():
            nn.init.uniform_(weight, -stdv, stdv)
        
        for name,param in self.named_parameters():
            if name=="rgate":
                param.data  = torch.tensor(0.8).to(device) 


        for i in range(self.num_chunks) :
            x = i * self.hidden_size
            for j in range(self.hidden_size) :
                if (j < 0.8*self.hidden_size) :
                    self.d_rec[x + j][j] = 1.0
                else :
                    self.d_rec[x + j][j] = -1.0


        

    def forward(self, input_, hx = None):

        if hx is None:
            hx = input_.new_zeros(self.num_chunks*self.hidden_size, requires_grad=False)

        dale_hh = torch.mm(self.relu(self.weight_hh), self.d_rec)
        if (self.bias) :
            w_x = self.bias_ih + torch.matmul(self.weight_ih,input_).t()
            w_h = self.bias_hh + torch.matmul(dale_hh,hx.t()).t()
        else :
            w_x = torch.matmul(self.weight_ih,input_).t()
            w_h = torch.matmul(dale_hh,hx.t()).t()    
        w_w = ((self.rgate) * hx) + ((1-(self.rgate)) * (w_x + w_h))
        h = self.relu(w_w)
        return h

class LSTM(nn.Module):


    def __init__(self, input_units, vocab_size, hidden_units=650,batch_size = 128, embedding_dim = 200, output_units = 10, num_layers = 1, dropout=0.2):
        super(LSTM, self).__init__()
        self.embedding_dim = embedding_dim
        self.input_units = input_units
        self.hidden_units = hidden_units
        self.output_units = output_units
        self.num_layers = num_layers
        self.dropout = dropout
        self.batch_size = batch_size
        self.embedding_layer = torch.nn.Embedding(vocab_size,self.embedding_dim).to(device)

        for layer in range(num_layers):
            layer_input_units = self.embedding_dim if layer == 0 else hidden_units
            cell = LstmModule(input_units = layer_input_units, output_units = output_units, hidden_units = hidden_units, batch_size = batch_size,embedding_dim=layer_input_units)
            setattr(self, 'cell_{}'.format(layer), cell)
        

        # self.dropout_layer = nn.Dropout(dropout)
        self.linear = nn.Linear(hidden_units, 2)
        # self.softmax = nn.Softmax(dim=0)
        self.reset_parameters()

    def get_cell(self, layer):
        return getattr(self, 'cell_{}'.format(layer))

    def reset_parameters(self):
        for layer in range(self.num_layers):
            cell = self.get_cell(layer)
            cell.reset_parameters()

    def forward(self, input_, max_time = 50) :
        # we will keep the format same as the LSTMs
        # we will pass on 2 outputs  - one being all_h at the last layer, the other being all h at last time stamp and the final one being the 
        # last h of the last layer on which we will apply the softmax. 
        # the input size will be of form (batch_size, feature_len)

        #max_time  =  min(input_.shape[1], max_time)
        h_n =[]
        m = input_.shape[0]
        #print("Input dimension: "+str(input_.shape))
        for layer in range(self.num_layers):
            state=None
            cell = self.get_cell(layer)
            all_hidden, all_outputs = [],[]
            for time in range(max_time):
                if layer==0:
                    input_emb = self.embedding_layer(input_[:,time].long())
                state = cell(input_ = input_emb.t(), hx = state)
                # print("inside state:"+str(state.shape))
                all_hidden.append(state.tolist())
                out = self.linear(state)
                all_outputs.append(out.tolist())

            h_n.append(state.tolist())
            input_emb=torch.tensor(all_hidden).to(device)
        
        hlast = state
        #all_outputs = torch.tensor(all_outputs).to(device)
        softmax_out = self.linear(hlast)
        h_n.reverse()
        return softmax_out, input_emb, torch.Tensor(h_n).to(device)
