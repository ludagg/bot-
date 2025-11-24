const express = require('express');
const path = require('path');
const { default: makeWASocket, DisconnectReason, useMultiFileAuthState } = require('@whiskeysockets/baileys');
const pino = require('pino');

const app = express();
const PORT = process.env.PORT || 3000;

// Variable pour stocker le QR code
let qrCodeData = null;
let isConnected = false;
let connectionStatus = 'Démarrage...';

// Servir les fichiers statiques
app.use(express.static('public'));

// API pour obtenir le QR code
app.get('/api/qr', (req, res) => {
    res.json({
        qr: qrCodeData,
        connected: isConnected,
        status: connectionStatus
    });
});

// API pour obtenir le statut
app.get('/api/status', (req, res) => {
    res.json({
        connected: isConnected,
        status: connectionStatus,
        timestamp: new Date().toISOString()
    });
});

// Démarrer le serveur Express
app.listen(PORT, () => {
    console.log(`🌐 Serveur web démarré sur le port ${PORT}`);
    console.log(`📱 Ouvrez http://localhost:${PORT} pour scanner le QR code`);
});

// Fonction pour le bot WhatsApp
async function connectToWhatsApp() {
    const { state, saveCreds } = await useMultiFileAuthState('auth_info_baileys');
    
    const sock = makeWASocket({
        auth: state,
        printQRInTerminal: false, // Désactivé car on utilise la web UI
        logger: pino({ level: 'silent' })
    });

    sock.ev.on('creds.update', saveCreds);

    sock.ev.on('connection.update', (update) => {
        const { connection, lastDisconnect, qr } = update;
        
        // Capturer le QR code
        if (qr) {
            qrCodeData = qr;
            connectionStatus = 'En attente du scan QR code...';
            console.log('📱 QR Code disponible sur l\'interface web');
        }
        
        if(connection === 'close') {
            isConnected = false;
            const shouldReconnect = lastDisconnect?.error?.output?.statusCode !== DisconnectReason.loggedOut;
            connectionStatus = shouldReconnect ? 'Reconnexion...' : 'Déconnecté';
            console.log('❌ Connexion fermée. Reconnexion:', shouldReconnect);
            
            if(shouldReconnect) {
                setTimeout(() => connectToWhatsApp(), 5000);
            } else {
                qrCodeData = null;
                connectionStatus = 'Déconnecté - Redémarrez le service';
            }
        } else if(connection === 'open') {
            isConnected = true;
            qrCodeData = null;
            connectionStatus = 'Connecté ✅';
            console.log('✅ Bot WhatsApp connecté avec succès!');
        } else if(connection === 'connecting') {
            connectionStatus = 'Connexion en cours...';
        }
    });

    sock.ev.on('messages.upsert', async (m) => {
        const msg = m.messages[0];
        
        if (!msg.message || msg.key.fromMe) return;
        
        const messageText = msg.message.conversation || 
                           msg.message.extendedTextMessage?.text || '';
        
        const from = msg.key.remoteJid;
        
        console.log('📩 Message reçu de', from, ':', messageText);
        
        // Logique du bot
        try {
            if (messageText.toLowerCase().includes('bonjour')) {
                await sock.sendMessage(from, { 
                    text: '👋 Bonjour ! Comment puis-je vous aider ?' 
                });
            } 
            else if (messageText.toLowerCase().includes('aide') || messageText.toLowerCase() === 'help') {
                await sock.sendMessage(from, { 
                    text: '🤖 *Commandes disponibles:*\n\n' +
                          '• bonjour - Saluer le bot\n' +
                          '• aide - Afficher cette aide\n' +
                          '• menu - Voir le menu\n' +
                          '• ping - Tester le bot\n' +
                          '• info - Informations sur le bot\n' +
                          '• citation - Recevoir une citation inspirante\n' +
                          '• blague - Entendre une blague'
                });
            }
            else if (messageText.toLowerCase().includes('menu')) {
                await sock.sendMessage(from, { 
                    text: '📋 *MENU PRINCIPAL*\n\n' +
                          '1️⃣ Service Client\n' +
                          '2️⃣ Informations Produits\n' +
                          '3️⃣ Support Technique\n' +
                          '4️⃣ Horaires d\'ouverture\n' +
                          '5️⃣ Contact\n\n' +
                          'Répondez avec le numéro de votre choix (1-5)'
                });
            }
            else if (messageText === '1') {
                await sock.sendMessage(from, { 
                    text: '📞 *Service Client*\n\n' +
                          'Notre équipe est disponible pour vous aider.\n' +
                          'Horaires: Lun-Ven 9h-18h\n' +
                          'Email: support@exemple.com\n' +
                          'Tél: +33 1 23 45 67 89'
                });
            }
            else if (messageText === '2') {
                await sock.sendMessage(from, { 
                    text: '📦 *Informations Produits*\n\n' +
                          'Consultez notre catalogue complet sur:\n' +
                          'www.exemple.com/produits\n\n' +
                          'Nouveautés disponibles !'
                });
            }
            else if (messageText === '3') {
                await sock.sendMessage(from, { 
                    text: '🔧 *Support Technique*\n\n' +
                          'Besoin d\'aide technique ?\n' +
                          'Décrivez votre problème et notre équipe vous répondra sous 24h.'
                });
            }
            else if (messageText === '4') {
                await sock.sendMessage(from, { 
                    text: '🕐 *Horaires d\'ouverture*\n\n' +
                          '📅 Lundi - Vendredi: 9h00 - 18h00\n' +
                          '📅 Samedi: 10h00 - 16h00\n' +
                          '📅 Dimanche: Fermé'
                });
            }
            else if (messageText === '5') {
                await sock.sendMessage(from, { 
                    text: '📧 *Contact*\n\n' +
                          '📍 Adresse: 123 Rue Example, Paris\n' +
                          '📞 Tél: +33 1 23 45 67 89\n' +
                          '📧 Email: contact@exemple.com\n' +
                          '🌐 Site: www.exemple.com'
                });
            }
            else if (messageText.toLowerCase().includes('ping')) {
                await sock.sendMessage(from, { 
                    text: '🏓 Pong! Le bot fonctionne parfaitement.\n⏰ ' + new Date().toLocaleString('fr-FR')
                });
            }
            else if (messageText.toLowerCase().includes('info')) {
                await sock.sendMessage(from, { 
                    text: '🤖 *Bot WhatsApp*\n\n' +
                          '✅ Statut: En ligne\n' +
                          '🔧 Version: 1.0.0\n' +
                          '⚡ Powered by Baileys\n' +
                          '📅 Actif 24/7'
                });
            }
            else if (messageText.toLowerCase().includes('citation')) {
                const citations = [
                    "Le succès c'est tomber sept fois, se relever huit. - Proverbe japonais",
                    "La vie est ce qui vous arrive pendant que vous êtes occupé à faire d'autres plans. - John Lennon",
                    "L'avenir appartient à ceux qui croient en la beauté de leurs rêves. - Eleanor Roosevelt",
                    "Soyez le changement que vous voulez voir dans le monde. - Gandhi",
                    "Le meilleur moment pour planter un arbre était il y a 20 ans. Le deuxième meilleur moment est maintenant. - Proverbe chinois"
                ];
                const citation = citations[Math.floor(Math.random() * citations.length)];
                await sock.sendMessage(from, { 
                    text: '✨ *Citation du jour*\n\n' + citation
                });
            }
            else if (messageText.toLowerCase().includes('blague')) {
                const blagues = [
                    "Qu'est-ce qu'un crocodile qui surveille une entrée ?\nUn Lacoste de sécurité ! 🐊",
                    "Pourquoi les plongeurs plongent-ils toujours en arrière ?\nParce que sinon ils tombent dans le bateau ! 🤿",
                    "Qu'est-ce qu'un canif ?\nUn petit fien ! 😄",
                    "Comment appelle-t-on un chat tombé dans un pot de peinture le jour de Noël ?\nUn chat-peint de Noël ! 🎨🐱",
                    "Qu'est-ce qu'un chou au milieu de l'océan ?\nUn chou marin ! 🥬🌊"
                ];
                const blague = blagues[Math.floor(Math.random() * blagues.length)];
                await sock.sendMessage(from, { 
                    text: '😄 *Blague du jour*\n\n' + blague
                });
            }
            else if (messageText.toLowerCase().includes('merci')) {
                await sock.sendMessage(from, { 
                    text: '😊 De rien ! Je suis là pour vous aider.\nN\'hésitez pas si vous avez d\'autres questions !'
                });
            }
            else if (messageText.toLowerCase().includes('salut') || messageText.toLowerCase().includes('hello')) {
                await sock.sendMessage(from, { 
                    text: '👋 Salut ! Comment allez-vous aujourd\'hui ?\nTapez "aide" pour voir ce que je peux faire pour vous.'
                });
            }
            else {
                await sock.sendMessage(from, { 
                    text: `Message reçu: "${messageText}"\n\n💡 Tapez "aide" pour voir les commandes disponibles ou "menu" pour le menu principal.` 
                });
            }
        } catch (error) {
            console.error('❌ Erreur lors de l\'envoi du message:', error);
        }
    });

    // Keep-alive
    setInterval(() => {
        console.log('💓 Bot actif -', new Date().toLocaleString('fr-FR'));
    }, 300000); // Toutes les 5 minutes
}

// Démarrer le bot WhatsApp
connectToWhatsApp();

// Gestion des erreurs
process.on('uncaughtException', (err) => {
    console.error('❌ Erreur non capturée:', err);
});

process.on('unhandledRejection', (err) => {
    console.error('❌ Promesse rejetée:', err);
});
