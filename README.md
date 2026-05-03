# 说明

## 安装

```bash
#>=3.12
$uv venv -p 3.12
$source .venv/bin/activate

#如果是在已经存在的环境中，建议先删除
#$uv pip uninstall opencv-python opencv-contrib-python opencv-contrib-headless opencv-contrib-python-headless
#$uv pip uninstall onnxruntime onnxruntime-gpu

#cpu版本
$uv pip install memect-ppx
$uv pip install onnxruntime --no-config
#or opencv-contrib-python-headless
$uv pip install opencv-contrib-python --no-config

#gpu版本
#安装依赖的cuda库，如果系统中已经全局安装，可以不安装，需要和onnxruntime-gpu的一致
#如果是其他版本，请根据onnxruntime-gpu的要求安装几个
$uv pip install memect-ppx[cuda]
#如果是windows且没有安装cuda，仅仅安装了direct12
#uv pip install onnxruntime-directml --no-config
#uv pip install install onnxruntime-cann
$uv pip install onnxruntime-gpu --no-config
#or opencv-contrib-python-headless
$uv pip install opencv-contrib-python --no-config


#安装方法二
$git clone 
$cd ppx
$uv venv -p 3.12
#如果操作系统比较老<=ubuntu 20.04
#--no-install-package pdf-oxide
$uv sync --no-install-project
#如果需要使用gpu，如果系统中已经全局安装，可以不安装，或者安装另外的版本
$uv sync --extra cuda --no-install-project

#这两个必须手动安装
#or opencv-contrib-python-headless
$uv pip install opencv-contrib-python --no-config
#or onnxruntime-gpu 
$uv pip install onnxruntime --no-config



#命令说明：
#安装包的方式，请使用: ppx
#clone代码的方式，linux/macOS请使用:./ppx
#clone代码的方式，windows请使用: ppx.bat

#默认解析
$ppx parse a.pdf

#大模型解析，指定url即可，目前仅仅支持deepseek-ocr，paddleocr-vl，glm-ocr等模型
$ppx parse a.pdf --llm http://127.0.0.1:4000/v1
#如果使用的模型的名字不包含deepseek，paddle，glm等，需要指定，如下：
$ppx parse a.pdf --llm '{"name":"deepseek","base_url":"http://127.0.0.1:4000/v1","model":"xxxx","api_key":""}'

#如果经常使用，可以写到配置文件中
$mkdir conf
#可以为json文件或者py文件: settings={}
#参考src/memect/conf/settings.custom.py 语法
$vi conf/settings.py
$vi conf/log.py

#如果在配置文件中写好了路径和模型等，就不需要在命令行再指定
$ppx parse a.pdf --backend deepseek
```

## 启动模型

```bash
#常用环境变量，可以附加在命令前面
$export CUDA_VISIBLE_DEVICES=0
#国内建议使用modelscope，下面的模型ID也是相对modelscope，huggingface的可能有所不同
$export VLLM_USE_MODELSCOPE=True

#这个选项的值，根据显存要求设置
#--gpu-memory-utilization

#需要大概20G
#https://modelscope.cn/models/deepseek-ai/DeepSeek-OCR-2
#不能够使用vllm==0.19.1执行，生成乱码
$vllm serve deepseek-ai/DeepSeek-OCR-2 --served-model-name deepseek-ocr-2 \
--logits-processors vllm.model_executor.models.deepseek_ocr:NGramPerReqLogitsProcessor \
--mm-processor-cache-gb 0 \
--no-enable-prefix-caching \
--port 4000 \
--gpu-memory-utilization 0.8 


#需要10G
#https://modelscope.cn/models/PaddlePaddle/PaddleOCR-VL
$vllm serve PaddlePaddle/PaddleOCR-VL \
  --served-model-name paddleocr-vl \
  --trust-remote-code \
  --max-num-batched-tokens 16384 \
  --no-enable-prefix-caching \
  --mm-processor-cache-gb 0 \
  --gpu-memory-utilization 0.5\
  --port 4001

#需要10G
#也可以启动1.5版本，模型名字，端口号等都一样，配置等就都不需要改变
#https://modelscope.cn/models/PaddlePaddle/PaddleOCR-VL-1.5
$vllm serve PaddlePaddle/PaddleOCR-VL-1.5 \
  --served-model-name paddleocr-vl \
  --trust-remote-code \
  --max-num-batched-tokens 16384 \
  --no-enable-prefix-caching \
  --mm-processor-cache-gb 0 \
  --gpu-memory-utilization 0.5\
  --port 4001

#https://modelscope.cn/models/ZhipuAI/GLM-OCR
$vllm serve ZhipuAI/GLM-OCR \
--served-model-name glmocr \
--max-num-batched-tokens 16384 \
--max-model-len 16384 \
--speculative-config '{"method": "mtp", "num_speculative_tokens": 1}' \
--gpu-memory-utilization 0.5 \
--port 4002
```
