from agreement_acceptor_decay_rnn import FullGramSentence
import filenames

pvn = FullGramSentence(filenames.deps, prop_train=0.8, output_filename='output_log.txt', len_after_verb=10, hidden_dim=100, embedding_dim=100)

pvn.pipeline(train=False,model="decay_fullGram_EXTREME_100_50_emb.pkl", load_data=False,load=True,epochs=10, model_prefix='decay_fullGram_EXTREME', data_name='fullGram', test_size=7000, test_external=True, load_external=True, external_file=filenames.external_file)
