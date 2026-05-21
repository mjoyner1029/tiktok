/**
 * ChatInterface - Conversational video creation component
 * 
 * Features:
 * - Send text messages to AI
 * - Upload videos/images
 * - Share TikTok URLs
 * - View AI responses
 * - Download completed renders
 */

import React, { useState, useEffect, useRef } from 'react';
import { 
  Box, 
  TextField, 
  Button, 
  Paper, 
  Typography, 
  IconButton,
  CircularProgress,
  List,
  ListItem,
  Avatar,
  Chip
} from '@mui/material';
import { 
  Send as SendIcon, 
  AttachFile as AttachFileIcon,
  SmartToy as BotIcon,
  Person as PersonIcon,
  Download as DownloadIcon
} from '@mui/icons-material';
import axios from 'axios';

interface Message {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  attachments?: Record<string, any>;
  response_metadata?: Record<string, any>;
  created_at: string;
}

interface Conversation {
  id: string;
  title: string;
  messages: Message[];
  project_id?: string;
}

const ChatInterface: React.FC = () => {
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [messageText, setMessageText] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Auto-scroll to bottom when new messages arrive
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [conversation?.messages]);

  // Create conversation on mount
  useEffect(() => {
    createConversation();
  }, []);

  const createConversation = async () => {
    try {
      const token = localStorage.getItem('access_token');
      const response = await axios.post(
        '/api/v1/chat/conversations',
        { title: 'New Video Project' },
        { headers: { Authorization: `Bearer ${token}` } }
      );
      setConversation(response.data);
    } catch (error) {
      console.error('Failed to create conversation:', error);
    }
  };

  const sendMessage = async () => {
    if (!messageText.trim() && selectedFiles.length === 0) return;
    if (!conversation) return;

    setIsLoading(true);

    try {
      const token = localStorage.getItem('access_token');

      // Upload files first if any
      if (selectedFiles.length > 0) {
        const formData = new FormData();
        selectedFiles.forEach(file => {
          formData.append('files', file);
        });

        await axios.post(
          `/api/v1/chat/conversations/${conversation.id}/upload`,
          formData,
          {
            headers: {
              Authorization: `Bearer ${token}`,
              'Content-Type': 'multipart/form-data',
            },
          }
        );

        setSelectedFiles([]);
      }

      // Send text message
      if (messageText.trim()) {
        const response = await axios.post(
          `/api/v1/chat/conversations/${conversation.id}/messages`,
          { content: messageText },
          { headers: { Authorization: `Bearer ${token}` } }
        );

        // Refresh conversation to get all messages
        const updatedConv = await axios.get(
          `/api/v1/chat/conversations/${conversation.id}`,
          { headers: { Authorization: `Bearer ${token}` } }
        );

        setConversation(updatedConv.data);
        setMessageText('');
      }
    } catch (error) {
      console.error('Failed to send message:', error);
    } finally {
      setIsLoading(false);
    }
  };

  const handleFileSelect = (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files || []);
    setSelectedFiles(prev => [...prev, ...files]);
  };

  const removeFile = (index: number) => {
    setSelectedFiles(prev => prev.filter((_, i) => i !== index));
  };

  const renderMessage = (message: Message) => {
    const isUser = message.role === 'user';
    const isSystem = message.role === 'system';

    return (
      <ListItem
        key={message.id}
        sx={{
          display: 'flex',
          flexDirection: isUser ? 'row-reverse' : 'row',
          alignItems: 'flex-start',
          gap: 1,
          mb: 2,
        }}
      >
        <Avatar sx={{ bgcolor: isUser ? 'primary.main' : 'secondary.main' }}>
          {isUser ? <PersonIcon /> : <BotIcon />}
        </Avatar>
        
        <Paper
          elevation={1}
          sx={{
            p: 2,
            maxWidth: '70%',
            bgcolor: isUser ? 'primary.light' : isSystem ? 'grey.100' : 'white',
            color: isUser ? 'primary.contrastText' : 'text.primary',
          }}
        >
          <Typography
            variant="body1"
            sx={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}
          >
            {message.content}
          </Typography>

          {/* Embedded video preview when a render is ready */}
          {message.response_metadata?.render_id && message.response_metadata?.download_path && (
            <Box sx={{ mt: 2 }}>
              <video
                controls
                style={{
                  width: '100%',
                  maxWidth: 360,
                  maxHeight: 640,
                  borderRadius: 8,
                  display: 'block',
                  background: '#000',
                }}
                poster={message.response_metadata?.thumbnail_path || undefined}
              >
                <source src={message.response_metadata.download_path} type="video/mp4" />
                Your browser does not support video playback.
              </video>
              <Button
                startIcon={<DownloadIcon />}
                variant="contained"
                color="success"
                size="small"
                sx={{ mt: 1 }}
                href={message.response_metadata.download_path}
                download
              >
                Download MP4
              </Button>
            </Box>
          )}

          {/* Legacy: show download button if only render_id (no download_path) */}
          {message.response_metadata?.render_id && !message.response_metadata?.download_path && (
            <Button
              startIcon={<DownloadIcon />}
              variant="contained"
              color="success"
              size="small"
              sx={{ mt: 1 }}
              href={`/api/v1/renders/${message.response_metadata.render_id}/download`}
              download
            >
              Download Video
            </Button>
          )}

          {/* Show project created indicator */}
          {message.response_metadata?.created_project && (
            <Chip
              label="Project Created"
              size="small"
              color="info"
              sx={{ mt: 1 }}
            />
          )}

          {/* Show job status */}
          {message.response_metadata?.import_job_id && (
            <Chip
              label="Analyzing Reference..."
              size="small"
              color="warning"
              sx={{ mt: 1 }}
            />
          )}
        </Paper>
      </ListItem>
    );
  };

  return (
    <Box
      sx={{
        display: 'flex',
        flexDirection: 'column',
        height: '100vh',
        maxWidth: '1200px',
        margin: '0 auto',
        p: 2,
      }}
    >
      {/* Header */}
      <Paper elevation={2} sx={{ p: 2, mb: 2 }}>
        <Typography variant="h5" gutterBottom>
          🎬 TikTok Style Engine
        </Typography>
        <Typography variant="body2" color="text.secondary">
          Upload your videos, share a TikTok you like, and I'll create a matching video!
        </Typography>
      </Paper>

      {/* Messages */}
      <Paper
        elevation={1}
        sx={{
          flex: 1,
          overflow: 'auto',
          p: 2,
          mb: 2,
          bgcolor: 'grey.50',
        }}
      >
        <List>
          {conversation?.messages.map(renderMessage)}
          <div ref={messagesEndRef} />
        </List>

        {isLoading && (
          <Box display="flex" justifyContent="center" p={2}>
            <CircularProgress size={24} />
          </Box>
        )}
      </Paper>

      {/* File Preview */}
      {selectedFiles.length > 0 && (
        <Paper elevation={1} sx={{ p: 2, mb: 2 }}>
          <Typography variant="subtitle2" gutterBottom>
            Selected Files:
          </Typography>
          <Box display="flex" gap={1} flexWrap="wrap">
            {selectedFiles.map((file, index) => (
              <Chip
                key={index}
                label={file.name}
                onDelete={() => removeFile(index)}
                color="primary"
                variant="outlined"
              />
            ))}
          </Box>
        </Paper>
      )}

      {/* Input Area */}
      <Paper elevation={2} sx={{ p: 2 }}>
        <Box display="flex" gap={1} alignItems="flex-end">
          {/* File Upload */}
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept="video/*,image/*"
            style={{ display: 'none' }}
            onChange={handleFileSelect}
          />
          <IconButton
            color="primary"
            onClick={() => fileInputRef.current?.click()}
            disabled={isLoading}
          >
            <AttachFileIcon />
          </IconButton>

          {/* Text Input */}
          <TextField
            fullWidth
            multiline
            maxRows={4}
            placeholder="Share a TikTok URL or ask me anything..."
            value={messageText}
            onChange={(e) => setMessageText(e.target.value)}
            onKeyPress={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
              }
            }}
            disabled={isLoading}
            variant="outlined"
            size="small"
          />

          {/* Send Button */}
          <Button
            variant="contained"
            color="primary"
            endIcon={<SendIcon />}
            onClick={sendMessage}
            disabled={isLoading || (!messageText.trim() && selectedFiles.length === 0)}
            sx={{ minWidth: '100px' }}
          >
            Send
          </Button>
        </Box>

        <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: 'block' }}>
          💡 Tip: Paste a TikTok URL, upload your videos/images, and I'll handle the rest!
        </Typography>
      </Paper>
    </Box>
  );
};

export default ChatInterface;
