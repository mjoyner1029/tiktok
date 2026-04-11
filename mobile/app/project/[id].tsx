/**
 * Project Editor Screen — 4-step TikTok creation workflow.
 * Step 0: Reference — import TikTok links/MP4s, AI analyzes editing style
 * Step 1: Upload — upload your own videos, images, audio
 * Step 2: Edit & Captions — view/edit AI-generated edit plan + captions
 * Step 3: Export — render & download final TikTok
 */
import React, { useState, useEffect, useCallback } from 'react';
import {
  View,
  Text,
  ScrollView,
  StyleSheet,
  TextInput,
  TouchableOpacity,
  Alert,
  ActivityIndicator,
  RefreshControl,
  Linking,
} from 'react-native';
import { useLocalSearchParams, useNavigation } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import * as ImagePicker from 'expo-image-picker';
import * as DocumentPicker from 'expo-document-picker';
import { Colors, Spacing, FontSize, Radius } from '../../src/theme';
import { Button, Badge, Card, Stepper } from '../../src/components';
import * as api from '../../src/services/api';
import type {
  Project,
  Asset,
  EditSpec,
  StyleProfile,
  Render,
  Job,
} from '../../src/services/api';

const STEPS = ['Reference', 'Upload', 'Edit', 'Export'];

export default function ProjectEditorScreen() {
  const { id: projectId } = useLocalSearchParams<{ id: string }>();
  const navigation = useNavigation();
  const [step, setStep] = useState(0);
  const [project, setProject] = useState<Project | null>(null);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [specs, setSpecs] = useState<EditSpec[]>([]);
  const [styles, setStyles] = useState<StyleProfile[]>([]);
  const [renders, setRenders] = useState<Render[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const refresh = useCallback(async () => {
    if (!projectId) return;
    try {
      const [p, a, sp, st, r, j] = await Promise.all([
        api.getProject(projectId),
        api.listAssets(projectId),
        api.listEditSpecs(projectId),
        api.listStyles(projectId),
        api.listRenders(projectId),
        api.listJobs(projectId),
      ]);
      setProject(p);
      setAssets(a);
      setSpecs(sp);
      setStyles(st);
      setRenders(r);
      setJobs(j);
    } catch {
      Alert.alert('Error', 'Failed to load project');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [projectId]);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 5000);
    return () => clearInterval(id);
  }, [refresh]);

  useEffect(() => {
    if (project) {
      navigation.setOptions({ title: project.title });
    }
  }, [project, navigation]);

  const referenceAssets = assets.filter((a) => a.type === 'reference_video');
  const userAssets = assets.filter((a) => a.type !== 'reference_video');
  const latestSpec = specs[0] || null;
  const latestRender = renders[0] || null;
  const completedSteps = [
    referenceAssets.length > 0 || styles.length > 0,
    userAssets.length > 0,
    latestSpec !== null,
    latestRender?.status === 'completed',
  ];

  if (loading || !project) {
    return (
      <View style={s.center}>
        <ActivityIndicator size="large" color={Colors.accent} />
      </View>
    );
  }

  return (
    <View style={s.container}>
      {/* Stepper */}
      <Stepper
        steps={STEPS}
        current={step}
        completed={completedSteps}
        onStepPress={setStep}
      />

      <ScrollView
        style={s.scroll}
        contentContainerStyle={s.scrollContent}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={() => { setRefreshing(true); refresh(); }}
            tintColor={Colors.accent}
          />
        }
      >
        {step === 0 && (
          <ReferenceStep
            projectId={project.id}
            assets={referenceAssets}
            styles={styles}
            jobs={jobs}
            onRefresh={refresh}
            onNext={() => setStep(1)}
          />
        )}
        {step === 1 && (
          <UploadStep
            projectId={project.id}
            assets={userAssets}
            onRefresh={refresh}
            onNext={() => setStep(2)}
          />
        )}
        {step === 2 && (
          <EditStep
            projectId={project.id}
            spec={latestSpec}
            allSpecs={specs}
            jobs={jobs}
            onRefresh={refresh}
            onNext={() => setStep(3)}
          />
        )}
        {step === 3 && (
          <ExportStep
            projectId={project.id}
            renders={renders}
            jobs={jobs}
            onRefresh={refresh}
          />
        )}
      </ScrollView>
    </View>
  );
}

// ═══════════════════════════════════════════════════════════════════════════
//  STEP 0: Reference TikToks
// ═══════════════════════════════════════════════════════════════════════════

function ReferenceStep({
  projectId,
  assets,
  styles: styleProfiles,
  jobs,
  onRefresh,
  onNext,
}: {
  projectId: string;
  assets: Asset[];
  styles: StyleProfile[];
  jobs: Job[];
  onRefresh: () => void;
  onNext: () => void;
}) {
  const [url, setUrl] = useState('');
  const [importing, setImporting] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [analyzing, setAnalyzing] = useState(false);
  const [showStyle, setShowStyle] = useState(false);

  const isAnalyzing = jobs.some(
    (j) => j.type === 'analyze_style' && (j.status === 'pending' || j.status === 'running')
  );

  const handlePickVideo = async () => {
    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ['videos'],
      allowsMultipleSelection: true,
      quality: 1,
    });
    if (result.canceled) return;
    setUploading(true);
    try {
      for (const asset of result.assets) {
        await api.uploadAsset(
          projectId,
          asset.uri,
          asset.fileName || 'reference.mp4',
          asset.mimeType || 'video/mp4',
          'reference_video'
        );
      }
      onRefresh();
    } catch (err: any) {
      Alert.alert('Upload Failed', err?.response?.data?.detail || 'Unknown error');
    } finally {
      setUploading(false);
    }
  };

  const handleImportUrl = async () => {
    if (!url.trim()) return;
    setImporting(true);
    try {
      await api.importFromUrl(projectId, url.trim());
      setUrl('');
      onRefresh();
    } catch (err: any) {
      Alert.alert('Import Failed', err?.response?.data?.detail || 'Could not import URL');
    } finally {
      setImporting(false);
    }
  };

  const handleAnalyze = async () => {
    setAnalyzing(true);
    try {
      await api.startAnalysis(projectId);
      onRefresh();
    } catch (err: any) {
      Alert.alert('Error', err?.response?.data?.detail || 'Analysis failed');
    } finally {
      setAnalyzing(false);
    }
  };

  const latestStyle = styleProfiles[0] || null;

  return (
    <View style={s.stepContainer}>
      <Text style={s.stepTitle}>Reference TikToks</Text>
      <Text style={s.stepDesc}>
        Drop in TikTok links or upload saved MP4s. The AI analyzes editing style — cuts,
        coloring, zoom patterns, captions, pacing.
      </Text>

      {/* URL Import */}
      <Card title="Paste Link" style={s.sectionCard}>
        <View style={s.urlRow}>
          <TextInput
            style={s.urlInput}
            placeholder="https://www.tiktok.com/@user/video/..."
            placeholderTextColor={Colors.textDim}
            value={url}
            onChangeText={setUrl}
            autoCapitalize="none"
            keyboardType="url"
          />
          <Button
            title="Import"
            variant="primary"
            size="sm"
            onPress={handleImportUrl}
            loading={importing}
            disabled={!url.trim()}
            icon={<Ionicons name="link" size={16} color={Colors.white} />}
          />
        </View>
      </Card>

      {/* Upload MP4 */}
      <TouchableOpacity style={s.uploadZone} onPress={handlePickVideo} activeOpacity={0.7}>
        {uploading ? (
          <ActivityIndicator color={Colors.accent} />
        ) : (
          <>
            <Ionicons name="cloud-upload-outline" size={36} color={Colors.textMuted} />
            <Text style={s.uploadTitle}>Upload Reference MP4s</Text>
            <Text style={s.uploadHint}>Tap to pick from your camera roll</Text>
          </>
        )}
      </TouchableOpacity>

      {/* Asset list */}
      {assets.length > 0 && (
        <Card title={`References (${assets.length})`} style={s.sectionCard}>
          {assets.map((a) => (
            <View key={a.id} style={s.assetRow}>
              <Ionicons name="film-outline" size={20} color={Colors.accent} />
              <View style={{ flex: 1 }}>
                <Text style={s.assetName} numberOfLines={1}>{a.filename}</Text>
                <Text style={s.assetMeta}>
                  {a.duration_sec ? `${a.duration_sec.toFixed(1)}s` : '—'}
                  {a.width ? ` · ${a.width}×${a.height}` : ''}
                </Text>
              </View>
              <Badge status={a.transcript_status} />
              <TouchableOpacity
                onPress={async () => {
                  await api.deleteAsset(a.id);
                  onRefresh();
                }}
                hitSlop={8}
              >
                <Ionicons name="trash-outline" size={16} color={Colors.textDim} />
              </TouchableOpacity>
            </View>
          ))}
        </Card>
      )}

      {/* Analyze button */}
      {assets.length > 0 && (
        <>
          <Button
            title={isAnalyzing ? 'Analyzing Style...' : 'Analyze Editing Style'}
            variant="primary"
            size="lg"
            onPress={handleAnalyze}
            loading={analyzing || isAnalyzing}
            icon={<Ionicons name="sparkles" size={18} color={Colors.white} />}
            style={s.actionBtn}
          />
          {latestStyle && (
            <TouchableOpacity onPress={() => setShowStyle(!showStyle)}>
              <Text style={s.toggleLink}>
                {showStyle ? 'Hide' : 'View'} Style Profile
              </Text>
            </TouchableOpacity>
          )}
          {latestStyle && showStyle && (
            <Card style={s.sectionCard}>
              <Text style={s.jsonText}>
                {JSON.stringify(latestStyle.profile_json, null, 2)}
              </Text>
            </Card>
          )}
        </>
      )}

      <Button
        title="Next: Upload Your Content"
        variant="secondary"
        size="lg"
        onPress={onNext}
        icon={<Ionicons name="arrow-forward" size={18} color={Colors.text} />}
        style={s.nextBtn}
      />
    </View>
  );
}

// ═══════════════════════════════════════════════════════════════════════════
//  STEP 1: Upload Your Content
// ═══════════════════════════════════════════════════════════════════════════

function UploadStep({
  projectId,
  assets,
  onRefresh,
  onNext,
}: {
  projectId: string;
  assets: Asset[];
  onRefresh: () => void;
  onNext: () => void;
}) {
  const [uploading, setUploading] = useState(false);

  const handlePickMedia = async (type: 'video' | 'image' | 'audio') => {
    if (type === 'audio') {
      const result = await DocumentPicker.getDocumentAsync({
        type: 'audio/*',
        multiple: true,
      });
      if (result.canceled) return;
      setUploading(true);
      try {
        for (const file of result.assets) {
          await api.uploadAsset(
            projectId,
            file.uri,
            file.name,
            file.mimeType || 'audio/mpeg',
            'audio'
          );
        }
        onRefresh();
      } catch (err: any) {
        Alert.alert('Upload Failed', err?.response?.data?.detail || 'Unknown error');
      } finally {
        setUploading(false);
      }
      return;
    }

    const mediaTypes: ImagePicker.MediaType[] =
      type === 'video' ? ['videos'] : ['images'];
    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes,
      allowsMultipleSelection: true,
      quality: 1,
    });
    if (result.canceled) return;
    setUploading(true);
    try {
      for (const asset of result.assets) {
        const assetType = type === 'video' ? 'raw_video' : 'image';
        await api.uploadAsset(
          projectId,
          asset.uri,
          asset.fileName || `${type}.${type === 'video' ? 'mp4' : 'jpg'}`,
          asset.mimeType || (type === 'video' ? 'video/mp4' : 'image/jpeg'),
          assetType
        );
      }
      onRefresh();
    } catch (err: any) {
      Alert.alert('Upload Failed', err?.response?.data?.detail || 'Unknown error');
    } finally {
      setUploading(false);
    }
  };

  const handleTranscribeAll = async () => {
    try {
      await api.transcribeAll(projectId);
      onRefresh();
    } catch (err: any) {
      Alert.alert('Error', err?.response?.data?.detail || 'Failed');
    }
  };

  const videoAssets = assets.filter((a) => a.type === 'raw_video');
  const imageAssets = assets.filter((a) => a.type === 'image');
  const audioAssets = assets.filter((a) => a.type === 'audio');

  return (
    <View style={s.stepContainer}>
      <Text style={s.stepTitle}>Upload Your Content</Text>
      <Text style={s.stepDesc}>
        Upload your own videos, images, and audio. These are the raw materials for your TikTok.
      </Text>

      {uploading && (
        <View style={s.uploadingBar}>
          <ActivityIndicator color={Colors.accent} size="small" />
          <Text style={s.uploadingText}>Uploading...</Text>
        </View>
      )}

      {/* Upload buttons */}
      <View style={s.uploadBtnRow}>
        <TouchableOpacity style={s.uploadBtn} onPress={() => handlePickMedia('video')}>
          <Ionicons name="videocam-outline" size={28} color={Colors.accent} />
          <Text style={s.uploadBtnLabel}>Video</Text>
        </TouchableOpacity>
        <TouchableOpacity style={s.uploadBtn} onPress={() => handlePickMedia('image')}>
          <Ionicons name="image-outline" size={28} color={Colors.accent2} />
          <Text style={s.uploadBtnLabel}>Image</Text>
        </TouchableOpacity>
        <TouchableOpacity style={s.uploadBtn} onPress={() => handlePickMedia('audio')}>
          <Ionicons name="musical-notes-outline" size={28} color={Colors.warning} />
          <Text style={s.uploadBtnLabel}>Audio</Text>
        </TouchableOpacity>
      </View>

      {/* Video assets */}
      {videoAssets.length > 0 && (
        <Card
          title={`Videos (${videoAssets.length})`}
          style={s.sectionCard}
          headerRight={
            <Button title="Transcribe All" variant="secondary" size="sm" onPress={handleTranscribeAll} />
          }
        >
          {videoAssets.map((a) => (
            <View key={a.id} style={s.assetRow}>
              <Ionicons name="film-outline" size={18} color={Colors.accent} />
              <View style={{ flex: 1 }}>
                <Text style={s.assetName} numberOfLines={1}>{a.filename}</Text>
                <Text style={s.assetMeta}>{a.duration_sec ? `${a.duration_sec.toFixed(1)}s` : '—'}</Text>
              </View>
              <Badge status={a.transcript_status} />
            </View>
          ))}
        </Card>
      )}

      {/* Image assets */}
      {imageAssets.length > 0 && (
        <Card title={`Images (${imageAssets.length})`} style={s.sectionCard}>
          {imageAssets.map((a) => (
            <View key={a.id} style={s.assetRow}>
              <Ionicons name="image-outline" size={18} color={Colors.accent2} />
              <Text style={[s.assetName, { flex: 1 }]} numberOfLines={1}>{a.filename}</Text>
            </View>
          ))}
        </Card>
      )}

      {/* Audio assets */}
      {audioAssets.length > 0 && (
        <Card title={`Audio (${audioAssets.length})`} style={s.sectionCard}>
          {audioAssets.map((a) => (
            <View key={a.id} style={s.assetRow}>
              <Ionicons name="musical-notes-outline" size={18} color={Colors.warning} />
              <View style={{ flex: 1 }}>
                <Text style={s.assetName} numberOfLines={1}>{a.filename}</Text>
                <Text style={s.assetMeta}>{a.duration_sec ? `${a.duration_sec.toFixed(1)}s` : '—'}</Text>
              </View>
            </View>
          ))}
        </Card>
      )}

      <Button
        title="Next: Edit & Captions"
        variant={assets.length > 0 ? 'primary' : 'secondary'}
        size="lg"
        onPress={onNext}
        disabled={assets.length === 0}
        icon={<Ionicons name="arrow-forward" size={18} color={assets.length > 0 ? Colors.white : Colors.text} />}
        style={s.nextBtn}
      />
    </View>
  );
}

// ═══════════════════════════════════════════════════════════════════════════
//  STEP 2: Edit Plan & Captions
// ═══════════════════════════════════════════════════════════════════════════

function EditStep({
  projectId,
  spec,
  allSpecs,
  jobs,
  onRefresh,
  onNext,
}: {
  projectId: string;
  spec: EditSpec | null;
  allSpecs: EditSpec[];
  jobs: Job[];
  onRefresh: () => void;
  onNext: () => void;
}) {
  const [generating, setGenerating] = useState(false);
  const [feedback, setFeedback] = useState('');
  const [revising, setRevising] = useState(false);
  const [userText, setUserText] = useState('');
  const [viewJson, setViewJson] = useState(false);

  const isAnalyzing = jobs.some(
    (j) => j.type === 'analyze_style' && (j.status === 'pending' || j.status === 'running')
  );

  const handleGenerate = async () => {
    setGenerating(true);
    try {
      await api.startAnalysis(projectId);
      onRefresh();
    } catch (err: any) {
      Alert.alert('Error', err?.response?.data?.detail || 'Generation failed');
    } finally {
      setGenerating(false);
    }
  };

  const handleRevise = async () => {
    if (!feedback.trim()) return;
    setRevising(true);
    try {
      await api.reviseEditSpec(projectId, feedback.trim());
      setFeedback('');
      onRefresh();
    } catch (err: any) {
      Alert.alert('Error', err?.response?.data?.detail || 'Revision failed');
    } finally {
      setRevising(false);
    }
  };

  const specJson = spec?.spec_json as any;
  const textTracks = specJson?.tracks?.text || [];
  const videoTracks = specJson?.tracks?.video || [];
  const audioTracks = specJson?.tracks?.audio || [];

  if (!spec) {
    return (
      <View style={s.stepContainer}>
        <Text style={s.stepTitle}>Edit Plan & Captions</Text>
        <View style={s.emptyStep}>
          <Ionicons name="create-outline" size={48} color={Colors.textDim} />
          <Text style={s.emptyStepTitle}>No edit plan yet</Text>
          <Text style={s.emptyStepDesc}>
            The AI will analyze your references and create a complete edit plan with cuts, zooms,
            captions, and audio mixing.
          </Text>
          <Button
            title={isAnalyzing ? 'AI is analyzing...' : 'Generate Edit Plan'}
            variant="primary"
            size="lg"
            onPress={handleGenerate}
            loading={generating || isAnalyzing}
            icon={<Ionicons name="sparkles" size={18} color={Colors.white} />}
          />
        </View>
      </View>
    );
  }

  return (
    <View style={s.stepContainer}>
      <View style={s.specHeader}>
        <Text style={s.stepTitle}>Edit Plan v{spec.version}</Text>
        <TouchableOpacity onPress={() => setViewJson(!viewJson)}>
          <Text style={s.toggleLink}>{viewJson ? 'Visual' : 'JSON'}</Text>
        </TouchableOpacity>
      </View>

      {spec.revision_note && (
        <Text style={s.revisionNote}>Last revision: "{spec.revision_note}"</Text>
      )}

      {viewJson ? (
        <Card style={s.sectionCard}>
          <ScrollView horizontal>
            <Text style={s.jsonText}>{JSON.stringify(specJson, null, 2)}</Text>
          </ScrollView>
        </Card>
      ) : (
        <>
          {/* Video track */}
          <Card title={`Video Track (${videoTracks.length})`} style={s.sectionCard}>
            {videoTracks.map((clip: any, i: number) => (
              <View key={i} style={s.clipRow}>
                <Text style={s.clipTime}>
                  {Number(clip.start).toFixed(1)}s–{Number(clip.end).toFixed(1)}s
                </Text>
                <Text style={s.clipLabel} numberOfLines={1}>{clip.asset_id}</Text>
                <Text style={s.clipMeta}>{clip.motion?.type || 'static'}</Text>
              </View>
            ))}
            {videoTracks.length === 0 && <Text style={s.emptyTrack}>No video clips</Text>}
          </Card>

          {/* Text / captions */}
          <Card title={`Captions (${textTracks.length})`} style={s.sectionCard}>
            {textTracks.map((clip: any, i: number) => (
              <View key={i} style={s.captionRow}>
                <Text style={s.clipTime}>
                  {Number(clip.start).toFixed(1)}s–{Number(clip.end).toFixed(1)}s
                </Text>
                <Text style={s.captionText} numberOfLines={2}>{clip.text}</Text>
                <Badge status={clip.position || 'lower_third'} />
              </View>
            ))}
            {textTracks.length === 0 && <Text style={s.emptyTrack}>No captions</Text>}
          </Card>

          {/* User text input */}
          <Card title="Your Text / Script" style={s.sectionCard}>
            <TextInput
              style={[s.textInput, { minHeight: 80 }]}
              placeholder="Type your script or caption text here..."
              placeholderTextColor={Colors.textDim}
              value={userText}
              onChangeText={setUserText}
              multiline
            />
          </Card>

          {/* Audio track */}
          <Card title={`Audio Track (${audioTracks.length})`} style={s.sectionCard}>
            {audioTracks.map((clip: any, i: number) => (
              <View key={i} style={s.clipRow}>
                <Text style={s.clipTime}>
                  {Number(clip.start).toFixed(1)}s–{Number(clip.end).toFixed(1)}s
                </Text>
                <Text style={s.clipLabel}>{clip.asset_id}</Text>
                <Text style={s.clipMeta}>
                  {clip.gain_db !== 0 ? `${clip.gain_db}dB` : '0dB'}
                </Text>
              </View>
            ))}
            {audioTracks.length === 0 && <Text style={s.emptyTrack}>No audio clips</Text>}
          </Card>
        </>
      )}

      {/* Revision */}
      <Card title="Revise with AI" style={s.sectionCard}>
        <Text style={s.assetMeta}>
          Tell the AI what to change — cut style, pacing, caption wording, zoom intensity, etc.
        </Text>
        <View style={[s.urlRow, { marginTop: Spacing.md }]}>
          <TextInput
            style={s.urlInput}
            placeholder='"Make cuts faster, more zoom-ins"'
            placeholderTextColor={Colors.textDim}
            value={feedback}
            onChangeText={setFeedback}
          />
          <Button
            title="Revise"
            variant="primary"
            size="sm"
            onPress={handleRevise}
            loading={revising}
            disabled={!feedback.trim()}
          />
        </View>
        {allSpecs.length > 1 && (
          <Text style={[s.assetMeta, { marginTop: Spacing.sm }]}>
            {allSpecs.length} versions — viewing v{spec.version}
          </Text>
        )}
      </Card>

      <Button
        title="Next: Export"
        variant="primary"
        size="lg"
        onPress={onNext}
        icon={<Ionicons name="arrow-forward" size={18} color={Colors.white} />}
        style={s.nextBtn}
      />
    </View>
  );
}

// ═══════════════════════════════════════════════════════════════════════════
//  STEP 3: Export / Render
// ═══════════════════════════════════════════════════════════════════════════

function ExportStep({
  projectId,
  renders,
  jobs,
  onRefresh,
}: {
  projectId: string;
  renders: Render[];
  jobs: Job[];
  onRefresh: () => void;
}) {
  const [rendering, setRendering] = useState(false);
  const [pipelining, setPipelining] = useState(false);

  const isRendering = jobs.some(
    (j) => j.type === 'render' && (j.status === 'pending' || j.status === 'running')
  );

  const handleRender = async () => {
    setRendering(true);
    try {
      await api.startRender(projectId);
      onRefresh();
    } catch (err: any) {
      Alert.alert('Error', err?.response?.data?.detail || 'Render failed');
    } finally {
      setRendering(false);
    }
  };

  const handleFullPipeline = async () => {
    setPipelining(true);
    try {
      await api.startFullPipeline(projectId);
      onRefresh();
    } catch (err: any) {
      Alert.alert('Error', err?.response?.data?.detail || 'Pipeline failed');
    } finally {
      setPipelining(false);
    }
  };

  const handleDownload = (renderId: string) => {
    const url = api.getDownloadUrl(renderId);
    Linking.openURL(url);
  };

  return (
    <View style={s.stepContainer}>
      <Text style={s.stepTitle}>Export Your TikTok</Text>
      <Text style={s.stepDesc}>
        Render the final video or run the full pipeline end-to-end.
      </Text>

      <View style={[s.uploadBtnRow, { marginBottom: Spacing.xxl }]}>
        <TouchableOpacity
          style={[s.uploadBtn, { borderColor: Colors.accent }]}
          onPress={handleRender}
          disabled={rendering || isRendering}
        >
          {isRendering ? (
            <ActivityIndicator color={Colors.accent} />
          ) : (
            <Ionicons name="film-outline" size={28} color={Colors.accent} />
          )}
          <Text style={s.uploadBtnLabel}>{isRendering ? 'Rendering...' : 'Render'}</Text>
        </TouchableOpacity>
        <TouchableOpacity
          style={[s.uploadBtn, { borderColor: Colors.accent2 }]}
          onPress={handleFullPipeline}
          disabled={pipelining || isRendering}
        >
          {pipelining ? (
            <ActivityIndicator color={Colors.accent2} />
          ) : (
            <Ionicons name="sync-outline" size={28} color={Colors.accent2} />
          )}
          <Text style={s.uploadBtnLabel}>Full Pipeline</Text>
        </TouchableOpacity>
      </View>

      {/* Render list */}
      {renders.map((r) => (
        <Card key={r.id} style={[s.sectionCard, { marginBottom: Spacing.md }]}>
          <View style={s.renderHeader}>
            <Badge status={r.status} />
            {r.duration_sec && (
              <Text style={s.assetMeta}>{r.duration_sec.toFixed(1)}s</Text>
            )}
            <Text style={s.assetMeta}>
              {new Date(r.created_at).toLocaleTimeString()}
            </Text>
          </View>

          {(r.status === 'queued' || r.status === 'rendering') && (
            <View style={s.progressBar}>
              <View
                style={[
                  s.progressFill,
                  { width: r.status === 'rendering' ? '60%' : '15%' },
                ]}
              />
            </View>
          )}

          {r.status === 'failed' && r.error_message && (
            <View style={s.errorBox}>
              <Text style={s.errorText}>{r.error_message}</Text>
            </View>
          )}

          {r.status === 'completed' && (
            <Button
              title="Download MP4"
              variant="success"
              size="lg"
              onPress={() => handleDownload(r.id)}
              icon={<Ionicons name="download-outline" size={18} color={Colors.bg} />}
              style={{ marginTop: Spacing.md }}
            />
          )}
        </Card>
      ))}

      {renders.length === 0 && !isRendering && (
        <View style={s.emptyStep}>
          <Ionicons name="film-outline" size={48} color={Colors.textDim} />
          <Text style={s.emptyStepTitle}>No renders yet</Text>
          <Text style={s.emptyStepDesc}>
            Tap "Render" to create your TikTok or "Full Pipeline" to run everything.
          </Text>
        </View>
      )}
    </View>
  );
}

// ═══════════════════════════════════════════════════════════════════════════
//  STYLES
// ═══════════════════════════════════════════════════════════════════════════

const s = StyleSheet.create({
  container: { flex: 1, backgroundColor: Colors.bg },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', backgroundColor: Colors.bg },
  scroll: { flex: 1 },
  scrollContent: { paddingBottom: 80 },

  stepContainer: { padding: Spacing.lg },
  stepTitle: { fontSize: FontSize.xxl, fontWeight: '700', color: Colors.text, marginBottom: Spacing.sm },
  stepDesc: { fontSize: FontSize.md, color: Colors.textMuted, marginBottom: Spacing.xxl, lineHeight: 20 },

  sectionCard: { marginBottom: Spacing.lg },

  // URL input
  urlRow: { flexDirection: 'row', gap: Spacing.sm, alignItems: 'center' },
  urlInput: {
    flex: 1,
    backgroundColor: Colors.bgElevated,
    borderWidth: 1,
    borderColor: Colors.border,
    borderRadius: Radius.sm,
    padding: Spacing.md,
    color: Colors.text,
    fontSize: FontSize.md,
  },

  // Upload zone
  uploadZone: {
    borderWidth: 2,
    borderStyle: 'dashed',
    borderColor: Colors.border,
    borderRadius: Radius.md,
    paddingVertical: 36,
    alignItems: 'center',
    gap: Spacing.sm,
    marginBottom: Spacing.lg,
  },
  uploadTitle: { fontSize: FontSize.lg, fontWeight: '600', color: Colors.text },
  uploadHint: { fontSize: FontSize.sm, color: Colors.textMuted },

  // Upload buttons row
  uploadBtnRow: {
    flexDirection: 'row',
    gap: Spacing.md,
    marginBottom: Spacing.lg,
  },
  uploadBtn: {
    flex: 1,
    backgroundColor: Colors.bgCard,
    borderWidth: 1,
    borderColor: Colors.border,
    borderRadius: Radius.md,
    paddingVertical: Spacing.xl,
    alignItems: 'center',
    gap: Spacing.sm,
  },
  uploadBtnLabel: { fontSize: FontSize.sm, fontWeight: '600', color: Colors.text },

  uploadingBar: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: Spacing.sm,
    padding: Spacing.md,
    backgroundColor: Colors.bgCard,
    borderRadius: Radius.sm,
    marginBottom: Spacing.lg,
  },
  uploadingText: { color: Colors.textMuted, fontSize: FontSize.sm },

  // Asset rows
  assetRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: Spacing.md,
    paddingVertical: Spacing.sm,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: Colors.border,
  },
  assetName: { fontSize: FontSize.md, fontWeight: '500', color: Colors.text },
  assetMeta: { fontSize: FontSize.sm, color: Colors.textMuted },

  // Actions
  actionBtn: { marginBottom: Spacing.md },
  nextBtn: { marginTop: Spacing.xxl, alignSelf: 'stretch' },

  toggleLink: { fontSize: FontSize.md, color: Colors.accent2, fontWeight: '600', textAlign: 'center', paddingVertical: Spacing.sm },

  // Spec / timeline
  specHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: Spacing.sm },
  revisionNote: { fontSize: FontSize.sm, color: Colors.textMuted, fontStyle: 'italic', marginBottom: Spacing.lg },

  clipRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: Spacing.sm,
    paddingVertical: Spacing.sm,
    paddingHorizontal: Spacing.sm,
    backgroundColor: Colors.bg,
    borderRadius: Radius.sm,
    marginBottom: Spacing.xs,
  },
  clipTime: { fontSize: FontSize.xs, fontFamily: 'Courier', color: Colors.accent2, width: 90 },
  clipLabel: { flex: 1, fontSize: FontSize.sm, color: Colors.text },
  clipMeta: { fontSize: FontSize.xs, color: Colors.textDim },

  captionRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: Spacing.sm,
    paddingVertical: Spacing.sm,
    paddingHorizontal: Spacing.sm,
    backgroundColor: Colors.bg,
    borderRadius: Radius.sm,
    marginBottom: Spacing.xs,
  },
  captionText: { flex: 1, fontSize: FontSize.md, fontWeight: '600', color: Colors.text },

  emptyTrack: { fontSize: FontSize.sm, color: Colors.textDim, textAlign: 'center', paddingVertical: Spacing.lg },

  textInput: {
    backgroundColor: Colors.bgElevated,
    borderWidth: 1,
    borderColor: Colors.border,
    borderRadius: Radius.sm,
    padding: Spacing.md,
    color: Colors.text,
    fontSize: FontSize.md,
    textAlignVertical: 'top',
  },

  jsonText: { fontSize: 11, fontFamily: 'Courier', color: Colors.accent2, lineHeight: 18 },

  // Empty
  emptyStep: { alignItems: 'center', paddingVertical: 40, gap: Spacing.sm },
  emptyStepTitle: { fontSize: FontSize.xl, fontWeight: '600', color: Colors.text, marginTop: Spacing.md },
  emptyStepDesc: { fontSize: FontSize.md, color: Colors.textMuted, textAlign: 'center', maxWidth: 300, lineHeight: 20 },

  // Render
  renderHeader: { flexDirection: 'row', alignItems: 'center', gap: Spacing.md },
  progressBar: { height: 6, backgroundColor: Colors.bgElevated, borderRadius: 3, marginTop: Spacing.md, overflow: 'hidden' },
  progressFill: { height: '100%', borderRadius: 3, backgroundColor: Colors.accent },
  errorBox: { backgroundColor: 'rgba(255,71,87,0.1)', padding: Spacing.md, borderRadius: Radius.sm, marginTop: Spacing.md },
  errorText: { fontSize: FontSize.sm, color: Colors.error },
});
