import { execFile } from 'child_process';
import { readEnvFile } from './env.js';

type TtsProvider = 'openai' | 'system';

interface TtsConfig {
  provider: TtsProvider;
  // OpenAI options
  openaiModel: string;
  openaiVoice: string;
  // System (macOS say) options
  systemVoice: string;
  systemRate: number;
}

function loadConfig(): TtsConfig {
  const env = readEnvFile([
    'TTS_PROVIDER',
    'TTS_OPENAI_VOICE',
    'TTS_SYSTEM_VOICE',
    'TTS_SYSTEM_RATE',
  ]);
  return {
    provider: (env.TTS_PROVIDER as TtsProvider) || 'openai',
    openaiModel: 'tts-1-hd',
    openaiVoice: env.TTS_OPENAI_VOICE || 'nova',
    systemVoice: env.TTS_SYSTEM_VOICE || 'Zuzana',
    systemRate: parseInt(env.TTS_SYSTEM_RATE || '240', 10),
  };
}

async function openaiTts(
  text: string,
  config: TtsConfig,
): Promise<Buffer | null> {
  const env = readEnvFile(['OPENAI_API_KEY']);
  const apiKey = env.OPENAI_API_KEY;

  if (!apiKey) {
    console.warn('OPENAI_API_KEY not set, skipping TTS');
    return null;
  }

  try {
    const openaiModule = await import('openai');
    const OpenAI = openaiModule.default;
    const openai = new OpenAI({ apiKey });

    const response = await openai.audio.speech.create({
      model: config.openaiModel,
      voice: config.openaiVoice as any,
      input: text,
      response_format: 'opus',
    });

    const arrayBuffer = await response.arrayBuffer();
    return Buffer.from(arrayBuffer);
  } catch (err) {
    console.error('OpenAI TTS failed:', err);
    return null;
  }
}

function systemTts(text: string, config: TtsConfig): Promise<Buffer | null> {
  return new Promise((resolve) => {
    // macOS say → AIFF → ffmpeg → opus in memory
    const sayArgs = [
      '-v',
      config.systemVoice,
      '-r',
      String(config.systemRate),
      '-o',
      '/dev/stdout',
      '--data-format=LEI16@22050',
      text,
    ];
    execFile(
      'say',
      sayArgs,
      { encoding: 'buffer', maxBuffer: 10 * 1024 * 1024 },
      (err, stdout) => {
        if (err || !stdout?.length) {
          console.error('System TTS (say) failed:', err);
          resolve(null);
          return;
        }
        // Convert raw audio to opus via ffmpeg
        const ffmpeg = execFile(
          'ffmpeg',
          [
            '-f',
            's16le',
            '-ar',
            '22050',
            '-ac',
            '1',
            '-i',
            'pipe:0',
            '-c:a',
            'libopus',
            '-b:a',
            '48k',
            '-f',
            'ogg',
            'pipe:1',
          ],
          { encoding: 'buffer', maxBuffer: 10 * 1024 * 1024 },
          (ffErr, ffOut) => {
            if (ffErr || !ffOut?.length) {
              console.error('System TTS ffmpeg conversion failed:', ffErr);
              resolve(null);
              return;
            }
            resolve(ffOut);
          },
        );
        ffmpeg.stdin?.write(stdout);
        ffmpeg.stdin?.end();
      },
    );
  });
}

export async function textToSpeech(text: string): Promise<Buffer | null> {
  const config = loadConfig();

  if (config.provider === 'system') {
    return systemTts(text, config);
  }
  return openaiTts(text, config);
}
