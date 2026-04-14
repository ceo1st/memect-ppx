from pathlib import Path
from typing import Any, Sequence, cast
from table_cls.utils.utils import OrtInferSession
from onnxruntime import InferenceSession
import onnxruntime
def _patch_init(self:OrtInferSession, model_path: str|Path, num_threads: int = -1,*,device_id:int=0,use_gpu:bool=True):
    self.verify_exist(model_path)

    self.num_threads = num_threads
    self._init_sess_opt()

    cpu_ep = "CPUExecutionProvider"
    cpu_provider_options = {
        "arena_extend_strategy": "kSameAsRequested",
    }

    
    providers:list[tuple[str,Any]]= []
    if use_gpu and onnxruntime.get_device()=='GPU':
        #TODO 因为现在为了修改简单，没有传递use_gpu参数，也就是总是为True
        #所以这个值目前可以理解为auto
        available_providers = cast(Sequence[str],onnxruntime.get_available_providers()) # type: ignore
        if 'CUDAExecutionProvider' in available_providers:
            providers.append(
                ('CUDAExecutionProvider', {'device_id': device_id})
            )
        elif 'MIGraphXExecutionProvider' in available_providers:
            #amd gpu
            #暂时不检查硬件了，如果没有gpu，只是显示警告，然后使用CPU
            providers.append(('MIGraphXExecutionProvider',{'device_id':device_id}))
        elif 'CANNExecutionProvider' in available_providers:
            #华为的gpu
            #暂时不检查硬件了，如果没有，只是显示警告，然后使用CPU
            providers.append(('CANNExecutionProvider',{'device_id':device_id}))
        else:
            #ROCMExecutionProvider
            #OpenVINOExecutionProvider
            #CoreMLExecutionProvider
            #raise ValueError(f'需要使用GPU，但是没有支持的provider，目前仅仅支持cuda,mix,cann,providers={available_providers}')
            pass

    providers.append((cpu_ep, cpu_provider_options))
    self.session = InferenceSession(
        str(model_path), sess_options=self.sess_opt, providers=providers
    )


OrtInferSession.__init__=_patch_init