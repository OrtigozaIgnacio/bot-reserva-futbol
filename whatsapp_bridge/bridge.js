const express = require('express');
const cors = require('cors');
const { Client, LocalAuth, MessageMedia } = require('whatsapp-web.js');
const axios = require('axios');
const fs = require('fs'); // NUEVO: Para manipular archivos
const path = require('path'); // NUEVO: Para las rutas de las carpetas

const app = express();
app.use(cors());
app.use(express.json());

// --- MEMORIA DEL ENJAMBRE ---
// Acá se guardan múltiples clientes en simultáneo (Ej: { "1": clienteA, "2": clienteB })
const sessions = {}; 
const qrCodes = {}; 
const statuses = {}; 

// Función para arrancar un bot específico
const startBot = (complejoId) => {
    if (sessions[complejoId]) return; // Si ya está corriendo, no lo duplicamos

    console.log(`🚀 [BOT ${complejoId}] Iniciando motor...`);
    statuses[complejoId] = 'INICIANDO';

    const client = new Client({
        // Esto guarda la sesión en una carpeta separada por cliente
        authStrategy: new LocalAuth({ clientId: `bot_${complejoId}` }),
        puppeteer: { 
            headless: true, // Asegura que corra sin interfaz gráfica
            args: [
                '--no-sandbox', 
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage', // 🛡️ EL ESCUDO VPS: Evita que crashee por falta de memoria RAM asignada
                '--disable-accelerated-2d-canvas', // No necesitamos gráficos 2D
                '--no-first-run', // Evita carteles de bienvenida de Chrome
                '--no-zygote', 
                '--disable-gpu' // Apaga la tarjeta de video, usamos solo CPU
            ] 
        }
    });

    client.on('qr', (qr) => {
        console.log(`📱 [BOT ${complejoId}] QR Generado. Esperando escaneo...`);
        qrCodes[complejoId] = qr;
        statuses[complejoId] = 'QR_LISTO';
    });

    client.on('ready', () => {
        console.log(`✅ [BOT ${complejoId}] ¡Conectado y Listo!`);
        qrCodes[complejoId] = null;
        statuses[complejoId] = 'CONECTADO';
    });

    // Función de Auto-Sanación
    const purgarMemoriaCorrupta = () => {
        try {
            const sessionPath = path.join(__dirname, '.wwebjs_auth', `session-bot_${complejoId}`);
            if (fs.existsSync(sessionPath)) {
                fs.rmSync(sessionPath, { recursive: true, force: true });
                console.log(`🗑️ [BOT ${complejoId}] Caché corrupto eliminado por el sistema.`);
            }
        } catch (err) {
            console.log(`⚠️ [BOT ${complejoId}] Error intentando borrar la carpeta:`, err.message);
        }
    };

    client.on('auth_failure', (msg) => {
        console.log(`🚨 [BOT ${complejoId}] Fallo de autenticación. WhatsApp cerró la sesión.`);
        statuses[complejoId] = 'DESCONECTADO';
        purgarMemoriaCorrupta();
        delete sessions[complejoId];
    });

    client.on('disconnected', (reason) => {
        console.log(`❌ [BOT ${complejoId}] Desconectado. Motivo: ${reason}`);
        statuses[complejoId] = 'DESCONECTADO';
        purgarMemoriaCorrupta();
        delete sessions[complejoId];
        client.destroy().catch(e => console.log("Error destruyendo cliente", e));
    });

    client.on('message', async (msg) => {
        if (msg.from === 'status@broadcast') return;
        
        const botNumber = client.info.wid.user;
        const userNumber = msg.from;

        // 1. Armamos el paquete si hay foto
        let mediaData = null; let mimeType = null; let hasMedia = false;
        if (msg.hasMedia) {
            try {
                const media = await msg.downloadMedia();
                mediaData = media.data; mimeType = media.mimetype; hasMedia = true;
            } catch (e) { console.log(`[BOT ${complejoId}] Error descargando foto`); }
        }

        const payload = {
            bot_phone_number: botNumber,
            phone_number: userNumber,
            message_body: msg.body || "",
            has_media: hasMedia,
            media_data: mediaData,
            mime_type: mimeType
        };

        // 2. Disparamos a FastAPI
        try {
            const response = await axios.post('http://127.0.0.1:8000/webhook', payload);
            
            // 3. Responder al jugador
            if (response.data && response.data.reply) {
                await client.sendMessage(msg.from, response.data.reply);
            }

            // 4. Alertar al dueño de la cancha / Enviar ticket de confirmación
            const ownerNotification = response.data.notify_owner;
            if (ownerNotification) {
                console.log("\n🔍 [DEBUG NOTIFICACIÓN] Se recibió una orden de envío desde Python.");
                const phoneList = ownerNotification.phones || [];
                
                for (const ownerPhone of phoneList) {
                    if (!ownerPhone) continue;
                    
                    try {
                        const rawPhone = String(ownerPhone).trim();
                        let safeJid = rawPhone;
                        
                        console.log(`🔍 [DEBUG] ID recibido para envío: "${rawPhone}"`);
                        
                        // Si NO es un ID interno (@lid), lo tratamos como un número normal y buscamos su formato real
                        if (!rawPhone.endsWith('@lid')) {
                            const cleanNumber = rawPhone.replace(/\D/g, ''); 
                            const registeredUser = await client.getNumberId(cleanNumber);
                            safeJid = registeredUser ? registeredUser._serialized : `${cleanNumber}@c.us`;
                            console.log(`🔍 [DEBUG] Número convertido a JID: "${safeJid}"`);
                        } else {
                            console.log(`🔍 [DEBUG] Es un ID de sistema (@lid), se usa directamente.`);
                        }
                        
                        // Ejecutamos el envío
                        console.log(`🚀 [DEBUG] Intentando enviar mensaje a ${safeJid}...`);
                        if (ownerNotification.media_data) {
                            const extension = ownerNotification.mime_type.split('/')[1].split(';')[0];
                            const mediaToSend = new MessageMedia(ownerNotification.mime_type, ownerNotification.media_data, `comprobante.${extension}`);
                            await client.sendMessage(safeJid, mediaToSend, { caption: ownerNotification.message });
                        } else {
                            await client.sendMessage(safeJid, ownerNotification.message);
                        }
                        console.log(`✅ [DEBUG] ¡Mensaje despachado con éxito a ${safeJid}!`);
                        
                    } catch (err) { 
                        console.log(`❌ [DEBUG ERROR] Falló el intento de envío:`, err.message); 
                    }
                }
            }
        } catch (error) {
            console.log(`[BOT ${complejoId}] Error enviando a FastAPI:`, error.message);
        }
    });

    client.initialize();
    sessions[complejoId] = client;
};

// --- ENDPOINTS PARA EL PANEL WEB ---
app.get('/api/bot/:id/status', (req, res) => {
    const id = req.params.id;
    res.json({
        status: statuses[id] || 'DESCONECTADO',
        qr: qrCodes[id] || null
    });
});

app.post('/api/bot/:id/start', (req, res) => {
    const id = req.params.id;
    startBot(id);
    res.json({ mensaje: 'Orden de encendido enviada.' });
});

// Iniciamos la API del puente
app.listen(3000, () => {
    console.log(`🚀 Motor WhatsApp Multi-Sesión escuchando en el puerto 3000`);
});