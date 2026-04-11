import React from 'react';
import { View, Text, StyleSheet, ViewStyle } from 'react-native';
import { Colors, Radius, FontSize, Spacing } from '../theme';

interface Props {
  status: string;
  style?: ViewStyle;
}

const STATUS_CONFIG: Record<string, { bg: string; text: string }> = {
  pending: { bg: 'rgba(136,136,168,0.15)', text: Colors.textMuted },
  running: { bg: 'rgba(108,92,231,0.15)', text: '#a29bfe' },
  processing: { bg: 'rgba(108,92,231,0.15)', text: '#a29bfe' },
  completed: { bg: 'rgba(0,214,143,0.15)', text: Colors.success },
  failed: { bg: 'rgba(255,71,87,0.15)', text: Colors.error },
  queued: { bg: 'rgba(255,184,0,0.15)', text: Colors.warning },
  draft: { bg: 'rgba(136,136,168,0.15)', text: Colors.textMuted },
  rendering: { bg: 'rgba(108,92,231,0.15)', text: '#a29bfe' },
};

export default function Badge({ status, style }: Props) {
  const config = STATUS_CONFIG[status] || STATUS_CONFIG.pending;
  return (
    <View style={[styles.badge, { backgroundColor: config.bg }, style]}>
      <Text style={[styles.text, { color: config.text }]}>{status}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  badge: {
    paddingVertical: 3,
    paddingHorizontal: Spacing.sm,
    borderRadius: Radius.full,
    alignSelf: 'flex-start',
  },
  text: {
    fontSize: FontSize.xs,
    fontWeight: '600',
    textTransform: 'capitalize',
  },
});
