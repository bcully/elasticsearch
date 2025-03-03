/*
 * Copyright Elasticsearch B.V. and/or licensed to Elasticsearch B.V. under one
 * or more contributor license agreements. Licensed under the Elastic License
 * 2.0; you may not use this file except in compliance with the Elastic License
 * 2.0.
 */

package org.elasticsearch.xpack.deprecation;

import org.elasticsearch.cluster.ClusterState;
import org.elasticsearch.cluster.metadata.DataStream;
import org.elasticsearch.cluster.metadata.DataStreamMetadata;
import org.elasticsearch.cluster.metadata.DataStreamOptions;
import org.elasticsearch.cluster.metadata.IndexMetadata;
import org.elasticsearch.cluster.metadata.Metadata;
import org.elasticsearch.cluster.metadata.MetadataIndexStateService;
import org.elasticsearch.common.collect.ImmutableOpenMap;
import org.elasticsearch.common.settings.Settings;
import org.elasticsearch.index.IndexMode;
import org.elasticsearch.index.IndexModule;
import org.elasticsearch.index.IndexSettings;
import org.elasticsearch.index.IndexVersion;
import org.elasticsearch.index.engine.frozen.FrozenEngine;
import org.elasticsearch.snapshots.SearchableSnapshotsSettings;
import org.elasticsearch.test.ESTestCase;
import org.elasticsearch.xpack.core.deprecation.DeprecationIssue;

import java.io.IOException;
import java.util.List;
import java.util.Map;

import static java.util.Collections.singletonList;
import static java.util.Collections.singletonMap;
import static org.elasticsearch.index.IndexModule.INDEX_STORE_TYPE_SETTING;
import static org.elasticsearch.xpack.deprecation.DeprecationChecks.INDEX_SETTINGS_CHECKS;
import static org.hamcrest.Matchers.empty;
import static org.hamcrest.Matchers.equalTo;
import static org.hamcrest.Matchers.hasItem;
import static org.hamcrest.collection.IsIterableContainingInOrder.contains;

public class IndexDeprecationChecksTests extends ESTestCase {
    public void testOldIndicesCheck() {
        IndexVersion createdWith = IndexVersion.fromId(7170099);
        IndexMetadata indexMetadata = IndexMetadata.builder("test")
            .settings(settings(createdWith))
            .numberOfShards(1)
            .numberOfReplicas(0)
            .build();
        ClusterState clusterState = ClusterState.builder(ClusterState.EMPTY_STATE)
            .metadata(Metadata.builder().put(indexMetadata, true))
            .build();
        DeprecationIssue expected = new DeprecationIssue(
            DeprecationIssue.Level.CRITICAL,
            "Old index with a compatibility version < 9.0",
            "https://www.elastic.co/guide/en/elasticsearch/reference/master/breaking-changes-9.0.html",
            "This index has version: " + createdWith.toReleaseVersion(),
            false,
            singletonMap("reindex_required", true)
        );
        List<DeprecationIssue> issues = DeprecationChecks.filterChecks(INDEX_SETTINGS_CHECKS, c -> c.apply(indexMetadata, clusterState));
        assertEquals(singletonList(expected), issues);
    }

    public void testOldIndicesCheckDataStreamIndex() {
        IndexVersion createdWith = IndexVersion.fromId(7170099);
        IndexMetadata indexMetadata = IndexMetadata.builder(".ds-test")
            .settings(settings(createdWith).put("index.hidden", true))
            .numberOfShards(1)
            .numberOfReplicas(0)
            .build();
        DataStream dataStream = new DataStream(
            randomAlphaOfLength(10),
            List.of(indexMetadata.getIndex()),
            randomNegativeLong(),
            Map.of(),
            randomBoolean(),
            false,
            false,
            randomBoolean(),
            randomFrom(IndexMode.values()),
            null,
            randomFrom(DataStreamOptions.EMPTY, DataStreamOptions.FAILURE_STORE_DISABLED, DataStreamOptions.FAILURE_STORE_ENABLED, null),
            List.of(),
            randomBoolean(),
            null
        );
        ClusterState clusterState = ClusterState.builder(ClusterState.EMPTY_STATE)
            .metadata(
                Metadata.builder()
                    .put(indexMetadata, true)
                    .customs(
                        Map.of(
                            DataStreamMetadata.TYPE,
                            new DataStreamMetadata(
                                ImmutableOpenMap.builder(Map.of("my-data-stream", dataStream)).build(),
                                ImmutableOpenMap.of()
                            )
                        )
                    )
            )
            .build();
        List<DeprecationIssue> issues = DeprecationChecks.filterChecks(INDEX_SETTINGS_CHECKS, c -> c.apply(indexMetadata, clusterState));
        assertThat(issues.size(), equalTo(0));
    }

    public void testOldIndicesCheckSnapshotIgnored() {
        IndexVersion createdWith = IndexVersion.fromId(7170099);
        Settings.Builder settings = settings(createdWith);
        settings.put(INDEX_STORE_TYPE_SETTING.getKey(), SearchableSnapshotsSettings.SEARCHABLE_SNAPSHOT_STORE_TYPE);
        IndexMetadata indexMetadata = IndexMetadata.builder("test").settings(settings).numberOfShards(1).numberOfReplicas(0).build();
        ClusterState clusterState = ClusterState.builder(ClusterState.EMPTY_STATE)
            .metadata(Metadata.builder().put(indexMetadata, true))
            .build();

        List<DeprecationIssue> issues = DeprecationChecks.filterChecks(INDEX_SETTINGS_CHECKS, c -> c.apply(indexMetadata, clusterState));

        assertThat(issues, empty());
    }

    public void testOldIndicesCheckClosedIgnored() {
        IndexVersion createdWith = IndexVersion.fromId(7170099);
        Settings.Builder settings = settings(createdWith);
        IndexMetadata indexMetadata = IndexMetadata.builder("test")
            .settings(settings)
            .numberOfShards(1)
            .numberOfReplicas(0)
            .state(IndexMetadata.State.CLOSE)
            .build();
        ClusterState clusterState = ClusterState.builder(ClusterState.EMPTY_STATE)
            .metadata(Metadata.builder().put(indexMetadata, true))
            .build();
        List<DeprecationIssue> issues = DeprecationChecks.filterChecks(INDEX_SETTINGS_CHECKS, c -> c.apply(indexMetadata, clusterState));
        assertThat(issues, empty());
    }

    public void testOldIndicesIgnoredWarningCheck() {
        IndexVersion createdWith = IndexVersion.fromId(7170099);
        Settings.Builder settings = settings(createdWith).put(MetadataIndexStateService.VERIFIED_READ_ONLY_SETTING.getKey(), true);
        IndexMetadata indexMetadata = IndexMetadata.builder("test").settings(settings).numberOfShards(1).numberOfReplicas(0).build();
        ClusterState clusterState = ClusterState.builder(ClusterState.EMPTY_STATE)
            .metadata(Metadata.builder().put(indexMetadata, true))
            .build();
        DeprecationIssue expected = new DeprecationIssue(
            DeprecationIssue.Level.WARNING,
            "Old index with a compatibility version < 9.0 Has Been Ignored",
            "https://www.elastic.co/guide/en/elasticsearch/reference/master/breaking-changes-9.0.html",
            "This read-only index has version: " + createdWith.toReleaseVersion() + " and will be supported as read-only in 9.0",
            false,
            singletonMap("reindex_required", true)
        );
        List<DeprecationIssue> issues = DeprecationChecks.filterChecks(INDEX_SETTINGS_CHECKS, c -> c.apply(indexMetadata, clusterState));
        assertEquals(singletonList(expected), issues);
    }

    public void testTranslogRetentionSettings() {
        Settings.Builder settings = settings(IndexVersion.current());
        settings.put(IndexSettings.INDEX_TRANSLOG_RETENTION_AGE_SETTING.getKey(), randomPositiveTimeValue());
        settings.put(IndexSettings.INDEX_TRANSLOG_RETENTION_SIZE_SETTING.getKey(), between(1, 1024) + "b");
        IndexMetadata indexMetadata = IndexMetadata.builder("test").settings(settings).numberOfShards(1).numberOfReplicas(0).build();
        List<DeprecationIssue> issues = DeprecationChecks.filterChecks(
            INDEX_SETTINGS_CHECKS,
            c -> c.apply(indexMetadata, ClusterState.EMPTY_STATE)
        );
        assertThat(
            issues,
            contains(
                new DeprecationIssue(
                    DeprecationIssue.Level.WARNING,
                    "translog retention settings are ignored",
                    "https://www.elastic.co/guide/en/elasticsearch/reference/current/index-modules-translog.html",
                    "translog retention settings [index.translog.retention.size] and [index.translog.retention.age] are ignored "
                        + "because translog is no longer used in peer recoveries with soft-deletes enabled (default in 7.0 or later)",
                    false,
                    DeprecationIssue.createMetaMapForRemovableSettings(
                        List.of(
                            IndexSettings.INDEX_TRANSLOG_RETENTION_SIZE_SETTING.getKey(),
                            IndexSettings.INDEX_TRANSLOG_RETENTION_AGE_SETTING.getKey()
                        )
                    )
                )
            )
        );
    }

    public void testDefaultTranslogRetentionSettings() {
        Settings.Builder settings = settings(IndexVersion.current());
        if (randomBoolean()) {
            settings.put(IndexSettings.INDEX_TRANSLOG_RETENTION_AGE_SETTING.getKey(), randomPositiveTimeValue());
            settings.put(IndexSettings.INDEX_TRANSLOG_RETENTION_SIZE_SETTING.getKey(), between(1, 1024) + "b");
            settings.put(IndexSettings.INDEX_SOFT_DELETES_SETTING.getKey(), false);
        }
        IndexMetadata indexMetadata = IndexMetadata.builder("test").settings(settings).numberOfShards(1).numberOfReplicas(0).build();
        List<DeprecationIssue> issues = DeprecationChecks.filterChecks(
            INDEX_SETTINGS_CHECKS,
            c -> c.apply(indexMetadata, ClusterState.EMPTY_STATE)
        );
        assertThat(issues, empty());
    }

    public void testIndexDataPathSetting() {
        Settings.Builder settings = settings(IndexVersion.current());
        settings.put(IndexMetadata.INDEX_DATA_PATH_SETTING.getKey(), createTempDir());
        IndexMetadata indexMetadata = IndexMetadata.builder("test").settings(settings).numberOfShards(1).numberOfReplicas(0).build();
        List<DeprecationIssue> issues = DeprecationChecks.filterChecks(
            INDEX_SETTINGS_CHECKS,
            c -> c.apply(indexMetadata, ClusterState.EMPTY_STATE)
        );
        final String expectedUrl =
            "https://www.elastic.co/guide/en/elasticsearch/reference/7.13/breaking-changes-7.13.html#deprecate-shared-data-path-setting";
        assertThat(
            issues,
            contains(
                new DeprecationIssue(
                    DeprecationIssue.Level.WARNING,
                    "setting [index.data_path] is deprecated and will be removed in a future version",
                    expectedUrl,
                    "Found index data path configured. Discontinue use of this setting.",
                    false,
                    null
                )
            )
        );
    }

    public void testSimpleFSSetting() {
        Settings.Builder settings = settings(IndexVersion.current());
        settings.put(IndexModule.INDEX_STORE_TYPE_SETTING.getKey(), "simplefs");
        IndexMetadata indexMetadata = IndexMetadata.builder("test").settings(settings).numberOfShards(1).numberOfReplicas(0).build();
        List<DeprecationIssue> issues = DeprecationChecks.filterChecks(
            INDEX_SETTINGS_CHECKS,
            c -> c.apply(indexMetadata, ClusterState.EMPTY_STATE)
        );
        assertThat(
            issues,
            contains(
                new DeprecationIssue(
                    DeprecationIssue.Level.WARNING,
                    "[simplefs] is deprecated and will be removed in future versions",
                    "https://www.elastic.co/guide/en/elasticsearch/reference/current/index-modules-store.html",
                    "[simplefs] is deprecated and will be removed in 8.0. Use [niofs] or other file systems instead. "
                        + "Elasticsearch 7.15 or later uses [niofs] for the [simplefs] store type "
                        + "as it offers superior or equivalent performance to [simplefs].",
                    false,
                    null
                )
            )
        );
    }

    public void testFrozenIndex() {
        Settings.Builder settings = settings(IndexVersion.current());
        settings.put(FrozenEngine.INDEX_FROZEN.getKey(), true);
        IndexMetadata indexMetadata = IndexMetadata.builder("test").settings(settings).numberOfShards(1).numberOfReplicas(0).build();
        List<DeprecationIssue> issues = DeprecationChecks.filterChecks(
            INDEX_SETTINGS_CHECKS,
            c -> c.apply(indexMetadata, ClusterState.EMPTY_STATE)
        );
        assertThat(
            issues,
            contains(
                new DeprecationIssue(
                    DeprecationIssue.Level.WARNING,
                    "index [test] is a frozen index. The frozen indices feature is deprecated and will be removed in a future version",
                    "https://www.elastic.co/guide/en/elasticsearch/reference/master/frozen-indices.html",
                    "Frozen indices no longer offer any advantages. Consider cold or frozen tiers in place of frozen indices.",
                    false,
                    null
                )
            )
        );
    }

    public void testCamelCaseDeprecation() throws IOException {
        String simpleMapping = "{\n\"_doc\": {"
            + "\"properties\" : {\n"
            + "   \"date_time_field\" : {\n"
            + "       \"type\" : \"date\",\n"
            + "       \"format\" : \"strictDateOptionalTime\"\n"
            + "       }\n"
            + "   }"
            + "} }";

        IndexMetadata simpleIndex = IndexMetadata.builder(randomAlphaOfLengthBetween(5, 10))
            .settings(settings(IndexVersion.current()))
            .numberOfShards(1)
            .numberOfReplicas(1)
            .putMapping(simpleMapping)
            .build();

        DeprecationIssue expected = new DeprecationIssue(
            DeprecationIssue.Level.CRITICAL,
            "Date fields use deprecated camel case formats",
            "https://ela.st/es-deprecation-7-camel-case-format",
            "Convert [date_time_field] format [strictDateOptionalTime] "
                + "which contains deprecated camel case to snake case. [strictDateOptionalTime] to [strict_date_optional_time].",
            false,
            null
        );
        List<DeprecationIssue> issues = DeprecationChecks.filterChecks(
            INDEX_SETTINGS_CHECKS,
            c -> c.apply(simpleIndex, ClusterState.EMPTY_STATE)
        );
        assertThat(issues, hasItem(expected));
    }
}
