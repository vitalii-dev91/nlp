python run.py --do_train --task nli --dataset snli --output_dir snli  --do_eval --per_device_train_batch_size 192  --per_device_eval_batch_size 192

python run.py --do_eval  --task nli --dataset snli --output_dir snli   --model ./old_snli/checkpoint-4500  --output_dir ./eval_output --per_device_eval_batch_size  192

// easy to learn 
python -m cartography.selection.train_dy_filtering  --filter --task_name SNLI  --model_dir models  --model ELECTRA  --metric confidence --worst

// ambiguious 
python -m cartography.selection.train_dy_filtering  --filter --task_name SNLI  --model_dir models  --model ELECTRA  --metric variability

// hard to learn 
python -m cartography.selection.train_dy_filtering  --filter --task_name SNLI  --model_dir models  --model ELECTRA  --metric confidence 

// ambiguious 
python run.py --do_eval  --task nli --dataset snli --output_dir snli   --model ./old_snli/checkpoint-4500  --output_dir null  --per_device_eval_batch_size  192  --subset  --eval_train  --metric confidence


// hard
python run.py --do_eval  --task nli --dataset snli --output_dir snli   --model ./old_snli/checkpoint-4500  --output_dir null  --per_device_eval_batch_size  192  --subset  --eval_train  --metric confidence


python run.py --do_train --task nli --dataset snli --output_dir hard_snli   --per_device_train_batch_size 192  --subset  --metric confidence  --do_eval

full eval: 0.6397073864936829 

66.95

// easy 
python run.py --do_eval  --task nli --dataset snli --output_dir snli   --model ./old_snli/checkpoint-4500  --output_dir null  --per_device_eval_batch_size  192  --subset  --eval_train  --metric confidence --worst

100 

full eval: 0.7829709649085999


ambiguious 0.8327575922012329