require('dotenv').config({ path: '../.env' });
const { Client, LocalAuth, MessageMedia } = require('whatsapp-web.js');
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

        // 4. NUEVO: Alerta paralela a todos los Dueños (Si Python lo solicita)
        const ownerNotification = response.data.notify_owner;
        if (ownerNotification) {
             const phoneList = ownerNotification.phones || [];
             
             for (const ownerPhone of phoneList) {
                 if (!ownerPhone) continue;
                 
                 try {
                     // 1. Limpiamos la basura (@c.us, @lid, espacios, signos +)
                     // Dejamos únicamente los números puros.
                     const cleanNumber = ownerPhone.replace(/\D/g, ''); 
                     
                     // 2. Le preguntamos a Meta cuál es el ID interno exacto de este número
                     const registeredUser = await client.getNumberId(cleanNumber);
                     
                     if (!registeredUser) {
                         console.log(`⚠️ WhatsApp dice que el número ${cleanNumber} no existe.`);
                         continue; // Saltamos al siguiente número de la lista
                     }
                     
                     // 3. Este es el ID perfecto y seguro (con o sin el 9, WhatsApp lo decide)
                     const safeJid = registeredUser._serialized; 
                     
                     if (ownerNotification.media_data) {
                         const extension = ownerNotification.mime_type.split('/')[1].split(';')[0];
                         const fileName = `comprobante.${extension}`;
                         const mediaToSend = new MessageMedia(ownerNotification.mime_type, ownerNotification.media_data, fileName);
                         
                         await client.sendMessage(safeJid, mediaToSend, { caption: ownerNotification.message });
                     } else {
                         await client.sendMessage(safeJid, ownerNotification.message);
                     }
                 } catch (err) {
                     console.log(`❌ Error interno al notificar a ${ownerPhone}:`, err.message);
                 }
             }
        }
        
    } catch (error) {
        console.error('❌ Error comunicándose con FastAPI:', error.message);
    }
});

client.initialize();