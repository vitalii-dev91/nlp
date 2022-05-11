import datasets
from transformers import AutoTokenizer, AutoModelForSequenceClassification, \
    AutoModelForQuestionAnswering, Trainer, TrainingArguments, HfArgumentParser
from helpers import prepare_dataset_nli, prepare_train_dataset_qa, \
    prepare_validation_dataset_qa, QuestionAnsweringTrainer, compute_accuracy
import os
import json
from scipy.special import softmax  
import pandas as pd 
from tqdm import tqdm 

NUM_PREPROCESSING_WORKERS = 4

def consider_ascending_order(filtering_metric: str) -> bool:
  """
  Determine if the metric values' sorting order to get the most `valuable` examples for training.
  """
  if filtering_metric == "variability":
    return False
  elif filtering_metric == "confidence":
    return True
  elif filtering_metric == "threshold_closeness":
    return False
  elif filtering_metric == "forgetfulness":
    return False
  elif filtering_metric == "correctness":
    return True
  else:
    raise NotImplementedError(f"Filtering based on {filtering_metric} not implemented!")

def subsample_dataset(args, train_dy_metrics, dataset):
    is_ascending = consider_ascending_order(args.metric)
    if args.worst:
        is_ascending = not is_ascending

    sorted_scores = train_dy_metrics.sort_values(by=[args.metric],
                                                ascending=is_ascending)

    num_samples = int(0.3319 * len(dataset))
    selected = sorted_scores.head(n=num_samples+1)

    indices = [] 

    for idx in tqdm(range(num_samples)):
        indices.append(int(selected.iloc[idx]["guid"]))

    sub_dataset = dataset.select(indices)
    return sub_dataset 

def main():
    argp = HfArgumentParser(TrainingArguments)
    # The HfArgumentParser object collects command-line arguments into an object (and provides default values for unspecified arguments).
    # In particular, TrainingArguments has several keys that you'll need/want to specify (when you call run.py from the command line):
    # --do_train
    #     When included, this argument tells the script to train a model.
    #     See docstrings for "--task" and "--dataset" for how the training dataset is selected.
    # --do_eval
    #     When included, this argument tells the script to evaluate the trained/loaded model on the validation split of the selected dataset.
    # --per_device_train_batch_size <int, default=8>
    #     This is the training batch size.
    #     If you're running on GPU, you should try to make this as large as you can without getting CUDA out-of-memory errors.
    #     For reference, with --max_length=128 and the default ELECTRA-small model, a batch size of 32 should fit in 4gb of GPU memory.
    # --num_train_epochs <float, default=3.0>
    #     How many passes to do through the training data.
    # --output_dir <path>
    #     Where to put the trained model checkpoint(s) and any eval predictions.
    #     *This argument is required*.

    argp.add_argument('--model', type=str,
                      default='google/electra-small-discriminator',
                      help="""This argument specifies the base model to fine-tune.
        This should either be a HuggingFace model ID (see https://huggingface.co/models)
        or a path to a saved model checkpoint (a folder containing config.json and pytorch_model.bin).""")
    argp.add_argument('--task', type=str, choices=['nli', 'qa'], required=True,
                      help="""This argument specifies which task to train/evaluate on.
        Pass "nli" for natural language inference or "qa" for question answering.
        By default, "nli" will use the SNLI dataset, and "qa" will use the SQuAD dataset.""")
    argp.add_argument('--dataset', type=str, default=None,
                      help="""This argument overrides the default dataset used for the specified task.""")
    argp.add_argument('--max_length', type=int, default=128,
                      help="""This argument limits the maximum sequence length used during training/evaluation.
        Shorter sequence lengths need less memory and computation time, but some examples may end up getting truncated.""")
    argp.add_argument('--max_train_samples', type=int, default=None,
                      help='Limit the number of examples to train on.')
    argp.add_argument('--max_eval_samples', type=int, default=None,
                      help='Limit the number of examples to evaluate on.')
    argp.add_argument("--eval_train", action='store_true')
    argp.add_argument("--subset", action='store_true')
    argp.add_argument("--worst",
                      action="store_true",
                      help="Select from the opposite end of the spectrum acc. to metric,"
                           "for baselines")

    argp.add_argument('--metric',
                      choices=('threshold_closeness',
                               'confidence',
                               'variability',
                               'correctness',
                               'forgetfulness'),
                      help="Metric to filter data by.",)


    training_args, args = argp.parse_args_into_dataclasses()

    # Dataset selection
    if args.dataset.endswith('.json') or args.dataset.endswith('.jsonl'):
        dataset_id = None
        # Load from local json/jsonl file
        dataset = datasets.load_dataset('json', data_files=args.dataset)
        # By default, the "json" dataset loader places all examples in the train split,
        # so if we want to use a jsonl file for evaluation we need to get the "train" split
        # from the loaded dataset
        eval_split = 'train'
    else:
        default_datasets = {'qa': ('squad',), 'nli': ('snli',)}
        dataset_id = tuple(args.dataset.split(':')) if args.dataset is not None else \
            default_datasets[args.task]
        # MNLI has two validation splits (one with matched domains and one with mismatched domains). Most datasets just have one "validation" split
        eval_split = 'validation_matched' if dataset_id == ('glue', 'mnli') else 'validation'
        # Load the raw data

        dataset = datasets.load_dataset(*dataset_id)
    
    # NLI models need to have the output label count specified (label 0 is "entailed", 1 is "neutral", and 2 is "contradiction")
    task_kwargs = {'num_labels': 3} if args.task == 'nli' else {}

    # Here we select the right model fine-tuning head
    model_classes = {'qa': AutoModelForQuestionAnswering,
                     'nli': AutoModelForSequenceClassification}
    model_class = model_classes[args.task]

    # Initialize the model and tokenizer from the specified pretrained model/checkpoint
    model = model_class.from_pretrained(args.model, **task_kwargs)
    tokenizer = AutoTokenizer.from_pretrained(args.model, use_fast=True)

    # Select the dataset preprocessing function (these functions are defined in helpers.py)
    if args.task == 'qa':
        prepare_train_dataset = lambda exs: prepare_train_dataset_qa(exs, tokenizer)
        prepare_eval_dataset = lambda exs: prepare_validation_dataset_qa(exs, tokenizer)
    elif args.task == 'nli':
        prepare_train_dataset = prepare_eval_dataset = \
            lambda exs: prepare_dataset_nli(exs, tokenizer, args.max_length)
        # prepare_eval_dataset = prepare_dataset_nli
    else:
        raise ValueError('Unrecognized task name: {}'.format(args.task))

    print("Preprocessing data... (this takes a little bit, should only happen once per dataset)")
    if dataset_id == ('snli',):
        # remove SNLI examples with no label
        dataset = dataset.filter(lambda ex: ex['label'] != -1)
    
    train_dataset = None
    eval_dataset = None
    train_dataset_featurized = None
    eval_dataset_featurized = None
    if training_args.do_train:
        train_dataset = dataset['train']
        if args.subset:
            train_dy_metrics = pd.read_pickle('resources/full.pkl')
            train_dataset = subsample_dataset(args, train_dy_metrics, train_dataset)

        if args.max_train_samples:
            train_dataset = train_dataset.select(range(args.max_train_samples))

        train_dataset_featurized = train_dataset.map(
            prepare_train_dataset,
            batched=True,
            num_proc=NUM_PREPROCESSING_WORKERS,
            remove_columns=train_dataset.column_names
        )
    if training_args.do_eval:
        if args.eval_train:
            eval_dataset = dataset['train']
            prepare_dataset = prepare_train_dataset
        else:
            eval_dataset = dataset[eval_split]
            prepare_dataset = prepare_eval_dataset

        if args.subset:
            train_dy_metrics = pd.read_pickle('resources/full.pkl')
            eval_dataset = subsample_dataset(args, train_dy_metrics, eval_dataset)


        import pdb; pdb.set_trace()

        if args.max_eval_samples:
            eval_dataset = eval_dataset.select(range(args.max_eval_samples))

        is_ascending = consider_ascending_order(args.metric)
        if args.worst:
            is_ascending = not is_ascending

        eval_dataset_featurized = eval_dataset.map(
            prepare_dataset,
            batched=True,
            num_proc=NUM_PREPROCESSING_WORKERS,
            remove_columns=eval_dataset.column_names
        )


    # Select the training configuration
    trainer_class = Trainer
    eval_kwargs = {}
    # If you want to use custom metrics, you should define your own "compute_metrics" function.
    # For an example of a valid compute_metrics function, see compute_accuracy in helpers.py.
    compute_metrics = None
    if args.task == 'qa':
        # For QA, we need to use a tweaked version of the Trainer (defined in helpers.py)
        # to enable the question-answering specific evaluation metrics
        trainer_class = QuestionAnsweringTrainer
        eval_kwargs['eval_examples'] = eval_dataset
        metric = datasets.load_metric('squad')
        compute_metrics = lambda eval_preds: metric.compute(
            predictions=eval_preds.predictions, references=eval_preds.label_ids)
    elif args.task == 'nli':
        compute_metrics = compute_accuracy
    

    # This function wraps the compute_metrics function, storing the model's predictions
    # so that they can be dumped along with the computed metrics
    eval_predictions = None
    def compute_metrics_and_store_predictions(eval_preds):
        nonlocal eval_predictions
        eval_predictions = eval_preds
        return compute_metrics(eval_preds)

    # Initialize the Trainer object with the specified arguments and the model and dataset we loaded above
    trainer = trainer_class(
        model=model,
        args=training_args,
        train_dataset=train_dataset_featurized,
        eval_dataset=eval_dataset_featurized,
        tokenizer=tokenizer,
        compute_metrics=compute_metrics_and_store_predictions
    )

    if training_args.do_train:
        trainer.train()
        trainer.save_model()

    if training_args.do_eval:
        results = trainer.evaluate(**eval_kwargs)
        print(f'Evaluation results: {args.model}')
        print(results)

        idx = int(str(args.model).split('-')[-1]) // 1500 - 1

        # os.makedirs(training_args.output_dir+'/training_dynamics', exist_ok=True)

        """with open(os.path.join(training_args.output_dir, 'training_dynamics',
            f'dynamics_epoch_{idx}.jsonl'), encoding='utf-8', mode='w') as f:

            for i, example in enumerate(eval_dataset):
                example_with_prediction = dict(example)
                # example_with_prediction['predicted_scores'] = eval_predictions.predictions[i].tolist()
                # example_with_prediction['predicted_label'] = int(eval_predictions.predictions[i].argmax())

                example_with_prediction['guid']= i 
                example_with_prediction[f'logits_epoch_{idx}'] = softmax(eval_predictions.predictions[i]).tolist()
                example_with_prediction['gold'] = example_with_prediction['label']
                example_with_prediction.pop('premise')
                example_with_prediction.pop('hypothesis')

                f.write(json.dumps(example_with_prediction))
                f.write('\n')
        """

if __name__ == "__main__":
    main()
