require('dotenv').config({ path: '../.env' });
const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
const axios = require('axios');

const client = new Client({
    authStrategy: new LocalAuth(),
    puppeteer: {
        headless: true,
        // Usamos barras normales (/) para que JavaScript no las elimine
        executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', 
        args: ['--no-sandbox', '--disable-setuid-sandbox']
    }
});

const FASTAPI_URL = process.env.FASTAPI_URL || 'http://localhost:8000/webhook';

client.on('qr', (qr) => {
    console.log('🤖 Escaneá el QR para vincular este número al SaaS:');
    qrcode.generate(qr, { small: true });
});

client.on('ready', () => {
    console.log(`✅ Bot conectado! Número: ${client.info.wid.user}`);
});

client.on('message', async (msg) => {
    if (msg.isGroupMsg || msg.isStatus) return;

    try {
        const botNumber = client.info.wid.user;
        const userNumber = msg.from;
        
        console.log("🕵️‍♂️ ID EXACTO DE WHATSAPP:", userNumber);

        // 1. Descargamos la imagen en Base64 si el jugador manda una
        let mediaData = null;
        let mimeType = null;
        if (msg.hasMedia) {
            const media = await msg.downloadMedia();
            if (media) {
                mediaData = media.data; 
                mimeType = media.mimetype;
            }
        }

        // 2. Enviamos el paquete completo a FastAPI
        const response = await axios.post(FASTAPI_URL, {
            bot_phone_number: botNumber,
            phone_number: userNumber,
            message_body: msg.body,
            has_media: msg.hasMedia,
            media_data: mediaData,
            mime_type: mimeType
        });

        // 3. Le respondemos al Jugador (El canal normal)
        const botReply = response.data.reply;
        if (botReply) {
             client.sendMessage(msg.from, botReply);
        }

        // 4. NUEVO: Alerta paralela al Dueño (Si Python lo solicita)
        const ownerNotification = response.data.notify_owner;
        if (ownerNotification) {
             const ownerPhone = ownerNotification.phone; 
             if (ownerNotification.media_data) {
                 // Extraemos la extensión real directamente del mime_type 
                 // (Ej: si es "image/png", recorta y guarda solo "png")
                 const extension = ownerNotification.mime_type.split('/')[1].split(';')[0];
                 const fileName = `comprobante.${extension}`;
                 
                 // Reconstruimos el archivo respetando su formato original 100%
                 const mediaToSend = new MessageMedia(ownerNotification.mime_type, ownerNotification.media_data, fileName);
                 
                 client.sendMessage(ownerPhone, mediaToSend, { caption: ownerNotification.message });
             } else {
                 client.sendMessage(ownerPhone, ownerNotification.message);
             }
        }
        
    } catch (error) {
        console.error('❌ Error comunicándose con FastAPI:', error.message);
    }
});

client.initialize();