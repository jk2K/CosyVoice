
import time
import io, os, sys
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append('{}/third_party/AcademiCodec'.format(ROOT_DIR))
sys.path.append('{}/third_party/Matcha-TTS'.format(ROOT_DIR))

import numpy as np
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import torch
import torchaudio

from cosyvoice.cli.cosyvoice import CosyVoice2
from cosyvoice.utils.file_utils import load_wav
import torchaudio
import ffmpeg

import json

cosyvoice = CosyVoice2('pretrained_models/CosyVoice2-0.5B')

default_voices = ['中文女', '中文男', '日语男', '粤语女', '英文女', '英文男', '韩语女']

spk_new = []

for name in os.listdir(f"{ROOT_DIR}/voices/"):
    print(name.replace(".py",""))
    spk_new.append(name.replace(".py",""))

print("默认音色",cosyvoice.list_available_spks())
print("自定义音色",spk_new)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def speed_change(input_audio: np.ndarray, speed: float, sr: int):
    # 检查输入数据类型和声道数
    if input_audio.dtype != np.int16:
        raise ValueError("输入音频数据类型必须为 np.int16")


    # 转换为字节流
    raw_audio = input_audio.astype(np.int16).tobytes()

    # 设置 ffmpeg 输入流
    input_stream = ffmpeg.input('pipe:', format='s16le', acodec='pcm_s16le', ar=str(sr), ac=1)

    # 变速处理
    output_stream = input_stream.filter('atempo', speed)

    # 输出流到管道
    out, _ = (
        output_stream.output('pipe:', format='s16le', acodec='pcm_s16le')
        .run(input=raw_audio, capture_stdout=True, capture_stderr=True)
    )

    # 将管道输出解码为 NumPy 数组
    processed_audio = np.frombuffer(out, np.int16)

    return processed_audio

@app.post("/")
async def sft_post(request: Request):
    question_data = await request.json()

    text = question_data.get('text')
    speaker = question_data.get('speaker')
    new = question_data.get('new',0)
    streaming = question_data.get('streaming',0)

    speed = request.query_params.get('speed',1.0)
    speed = float(speed)
    

    if not text:
        return {"error": "文本不能为空"}, 400

    if not speaker:
        return {"error": "角色名不能为空"}, 400

    # 非流式
    if streaming == 0:

        start = time.process_time()
        # 使用 next() 获取生成器的第一个值
        output = next(cosyvoice.inference_sft(text,speaker))
        end = time.process_time()
        print("infer time:", end - start)
        buffer = io.BytesIO()

        if speed != 1.0:
            try:
                numpy_array = output['tts_speech'].numpy()
                audio = (numpy_array * 32768).astype(np.int16) 
                audio_data = speed_change(audio, speed=speed, sr=int(22050))
                audio_data = torch.from_numpy(audio_data)
                audio_data = audio_data.reshape(1, -1)
            except Exception as e:
                print(f"Failed to change speed of audio: \n{e}")
        else:
            audio_data = output['tts_speech']

        torchaudio.save(buffer,audio_data, 22050, format="wav")
        buffer.seek(0)
        return Response(buffer.read(), media_type="audio/wav")

    # 流式模式
    else:

        spk_id = speaker

        if new:
            spk_id = "中文女"

        joblist = cosyvoice.frontend.text_normalize_stream(text, split=True)

        async def generate():
        
            for i in joblist:
                print(i)
                print("流式0")
                tts_speeches = []
                model_input = cosyvoice.frontend.frontend_sft(i, spk_id)
                if new:
                    # 加载数据
                    newspk = torch.load(f'{ROOT_DIR}/voices/{speaker}.pt')

                    model_input["flow_embedding"] = newspk["flow_embedding"]
                    model_input["llm_embedding"] = newspk["llm_embedding"]

                    model_input["llm_prompt_speech_token"] = newspk["llm_prompt_speech_token"]
                    model_input["llm_prompt_speech_token_len"] = newspk["llm_prompt_speech_token_len"]

                    model_input["flow_prompt_speech_token"] = newspk["flow_prompt_speech_token"]
                    model_input["flow_prompt_speech_token_len"] = newspk["flow_prompt_speech_token_len"]

                    model_input["prompt_speech_feat_len"] = newspk["prompt_speech_feat_len"]
                    model_input["prompt_speech_feat"] = newspk["prompt_speech_feat"]
                    model_input["prompt_text"] = newspk["prompt_text"]
                    model_input["prompt_text_len"] = newspk["prompt_text_len"]

                model_output = next(cosyvoice.model.inference_stream(**model_input))
                # print(model_input)
                tts_speeches.append(model_output['tts_speech'])
                output = torch.concat(tts_speeches, dim=1)
                buffer = io.BytesIO()
                if speed != 1.0:
                    try:
                        numpy_array = output.numpy()
                        audio = (numpy_array * 32768).astype(np.int16) 
                        audio_data = speed_change(audio, speed=speed, sr=int(22050))
                        audio_data = torch.from_numpy(audio_data)
                        audio_data = audio_data.reshape(1, -1)
                    except Exception as e:
                        print(f"Failed to change speed of audio: \n{e}")
                else:
                    audio_data = output

                torchaudio.save(buffer,audio_data, 22050, format="ogg")
                buffer.seek(0)

                yield buffer.read()

        return StreamingResponse(generate(), media_type="audio/ogg")
        # response = make_response(generate())
        # response.headers['Content-Type'] = 'audio/ogg'
        # response.headers['Content-Disposition'] = 'attachment; filename=sound.ogg'
        # return response


@app.get("/")
async def sft_get(request: Request):

    text = request.query_params.get('text')
    speaker = request.query_params.get('speaker')
    new = request.query_params.get('new',0)
    streaming = request.query_params.get('streaming',0)
    speed = request.query_params.get('speed',1.0)
    speed = float(speed)

    if not text:
        raise HTTPException(status_code=400, detail="文本不能为空")

    if not speaker:
        raise HTTPException(status_code=400, detail="角色名不能为空")

    # 非流式
    if streaming == 0:

        start = time.process_time()
        output = next(cosyvoice.inference_sft(text,speaker,speaker))
        end = time.process_time()
        print("infer time:", end - start)
        buffer = io.BytesIO()

        if speed != 1.0:
            try:
                numpy_array = output['tts_speech'].numpy()
                audio = (numpy_array * 32768).astype(np.int16) 
                audio_data = speed_change(audio, speed=speed, sr=int(22050))
                audio_data = torch.from_numpy(audio_data)
                audio_data = audio_data.reshape(1, -1)
            except Exception as e:
                print(f"Failed to change speed of audio: \n{e}")
        else:
            audio_data = output['tts_speech']

        torchaudio.save(buffer,audio_data, 22050, format="wav")
        buffer.seek(0)
        return Response(buffer.read(), mimetype="audio/wav")

    # 流式模式
    else:

        spk_id = speaker

        if new:
            spk_id = "中文女"

        joblist = cosyvoice.frontend.text_normalize_stream(text, split=True)

        def generate():
        
            for i in joblist:
                print(i)
                print("流式0")
                tts_speeches = []
                model_input = cosyvoice.frontend.frontend_sft(i, spk_id)
                if new:
                    # 加载数据
                    newspk = torch.load(f'{ROOT_DIR}/voices/{speaker}.pt', map_location=torch.device('cpu'))

                    model_input["flow_embedding"] = newspk["flow_embedding"]
                    model_input["llm_embedding"] = newspk["llm_embedding"]

                    model_input["llm_prompt_speech_token"] = newspk["llm_prompt_speech_token"]
                    model_input["llm_prompt_speech_token_len"] = newspk["llm_prompt_speech_token_len"]

                    model_input["flow_prompt_speech_token"] = newspk["flow_prompt_speech_token"]
                    model_input["flow_prompt_speech_token_len"] = newspk["flow_prompt_speech_token_len"]

                    model_input["prompt_speech_feat_len"] = newspk["prompt_speech_feat_len"]
                    model_input["prompt_speech_feat"] = newspk["prompt_speech_feat"]
                    model_input["prompt_text"] = newspk["prompt_text"]
                    model_input["prompt_text_len"] = newspk["prompt_text_len"]

                model_output = next(cosyvoice.model.inference_stream(**model_input))
                # print(model_input)
                tts_speeches.append(model_output['tts_speech'])
                output = torch.concat(tts_speeches, dim=1)
                buffer = io.BytesIO()
                if speed != 1.0:
                    try:
                        numpy_array = output.numpy()
                        audio = (numpy_array * 32768).astype(np.int16) 
                        audio_data = speed_change(audio, speed=speed, sr=int(22050))
                        audio_data = torch.from_numpy(audio_data)
                        audio_data = audio_data.reshape(1, -1)
                    except Exception as e:
                        print(f"Failed to change speed of audio: \n{e}")
                else:
                    audio_data = output

                torchaudio.save(buffer,audio_data, 22050, format="ogg")
                buffer.seek(0)

                yield buffer.read()

        return StreamingResponse(generate(), media_type="audio/ogg")

                





@app.post("/tts_to_audio/")
async def tts_to_audio(request: Request):

    import speaker_config
    
    question_data = await request.json()

    text = question_data.get('text')
    speaker = speaker_config.speaker
    new = speaker_config.new

    speed = speaker_config.speed
    

    if not text:
        raise HTTPException(status_code=400, detail="文本不能为空")
    if not speaker:
        raise HTTPException(status_code=400, detail="角色名不能为空")
    
    start = time.process_time()
    if not new:
        output = next(cosyvoice.inference_sft(text,speaker,"无"))
    else:
        output = next(cosyvoice.inference_sft(text,speaker,speaker))
    end = time.process_time()
    print("infer time:", end - start)
    buffer = io.BytesIO()
    if speed != 1.0:
        try:
            numpy_array = output['tts_speech'].numpy()
            audio = (numpy_array * 32768).astype(np.int16) 
            audio_data = speed_change(audio, speed=speed, sr=int(22050))
            audio_data = torch.from_numpy(audio_data)
            audio_data = audio_data.reshape(1, -1)
        except Exception as e:
            print(f"Failed to change speed of audio: \n{e}")
    else:
        audio_data = output['tts_speech']

    torchaudio.save(buffer,audio_data, 22050, format="wav")
    buffer.seek(0)
    return Response(buffer.read(), mimetype="audio/wav")



@app.get("/speakers")
async def speakers():

    voices = []

    for x in default_voices:
        voices.append({"name":x,"voice_id":x})

    for name in os.listdir("voices"):
        name = name.replace(".pt","")
        voices.append({"name":name,"voice_id":name})

    return JSONResponse(content=voices)


@app.get('/file/{filename}')
async def uploaded_file(filename: str):
    file_path = os.path.join("音频输出", filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    return Response(content=open(file_path, 'rb').read(), media_type="application/octet-stream")


@app.get("/speakers_list")
async def speakers_list():

    return JSONResponse(content=["female_calm", "female", "male"])
    

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=9880)
