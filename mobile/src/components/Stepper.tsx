import React from 'react';
import { View, Text, TouchableOpacity, StyleSheet, ScrollView } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { Colors, Spacing, FontSize, Radius } from '../theme';

interface Props {
  steps: string[];
  current: number;
  completed: boolean[];
  onStepPress: (index: number) => void;
}

export default function Stepper({ steps, current, completed, onStepPress }: Props) {
  return (
    <ScrollView
      horizontal
      showsHorizontalScrollIndicator={false}
      contentContainerStyle={styles.container}
    >
      {steps.map((label, i) => {
        const isActive = i === current;
        const isDone = completed[i];
        return (
          <TouchableOpacity
            key={label}
            style={[
              styles.step,
              isActive && styles.stepActive,
              isDone && styles.stepCompleted,
            ]}
            onPress={() => onStepPress(i)}
            activeOpacity={0.7}
          >
            <View
              style={[
                styles.number,
                isActive && styles.numberActive,
                isDone && styles.numberCompleted,
              ]}
            >
              {isDone ? (
                <Ionicons name="checkmark" size={12} color={Colors.bg} />
              ) : (
                <Text
                  style={[
                    styles.numberText,
                    isActive && styles.numberTextActive,
                  ]}
                >
                  {i + 1}
                </Text>
              )}
            </View>
            <Text
              style={[
                styles.label,
                isActive && styles.labelActive,
                isDone && styles.labelCompleted,
              ]}
              numberOfLines={1}
            >
              {label}
            </Text>
          </TouchableOpacity>
        );
      })}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: {
    flexDirection: 'row',
    gap: Spacing.sm,
    paddingHorizontal: Spacing.lg,
    paddingVertical: Spacing.md,
  },
  step: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: Spacing.sm,
    paddingVertical: Spacing.sm,
    paddingHorizontal: Spacing.md,
    backgroundColor: Colors.bgCard,
    borderWidth: 1,
    borderColor: Colors.border,
    borderRadius: Radius.sm,
    minWidth: 100,
  },
  stepActive: {
    borderColor: Colors.accent,
    backgroundColor: 'rgba(254,44,85,0.08)',
  },
  stepCompleted: {
    borderColor: Colors.success,
  },
  number: {
    width: 22,
    height: 22,
    borderRadius: 11,
    backgroundColor: Colors.bgElevated,
    alignItems: 'center',
    justifyContent: 'center',
  },
  numberActive: {
    backgroundColor: Colors.accent,
  },
  numberCompleted: {
    backgroundColor: Colors.success,
  },
  numberText: {
    fontSize: 11,
    fontWeight: '700',
    color: Colors.textMuted,
  },
  numberTextActive: {
    color: Colors.white,
  },
  label: {
    fontSize: FontSize.sm,
    fontWeight: '600',
    color: Colors.textMuted,
  },
  labelActive: {
    color: Colors.accent,
  },
  labelCompleted: {
    color: Colors.success,
  },
});
