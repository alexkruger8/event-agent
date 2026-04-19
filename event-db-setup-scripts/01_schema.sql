--
-- PostgreSQL database dump
--


-- Dumped from database version 17.9 (Debian 17.9-1.pgdg13+1)
-- Dumped by pg_dump version 17.9 (Debian 17.9-1.pgdg13+1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: anomalies; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.anomalies (
    id uuid NOT NULL,
    tenant_id uuid,
    metric_id uuid,
    metric_name character varying,
    metric_timestamp timestamp without time zone,
    current_value double precision,
    baseline_value double precision,
    deviation_percent double precision,
    severity character varying,
    detected_at timestamp without time zone,
    acknowledged_at timestamp without time zone,
    resolved_at timestamp without time zone,
    context jsonb
);


--
-- Name: conversations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.conversations (
    id uuid NOT NULL,
    tenant_id uuid,
    insight_id uuid,
    channel character varying,
    created_at timestamp without time zone
);


--
-- Name: errors; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.errors (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    error_type character varying NOT NULL,
    message text NOT NULL,
    stack_trace text,
    service character varying,
    component character varying,
    severity character varying DEFAULT 'error'::character varying NOT NULL,
    fingerprint character varying(64),
    occurrence_count integer DEFAULT 1 NOT NULL,
    first_seen_at timestamp without time zone NOT NULL,
    last_seen_at timestamp without time zone NOT NULL,
    resolved_at timestamp without time zone,
    metadata jsonb,
    ingested_at timestamp without time zone NOT NULL
);


--
-- Name: event_types; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.event_types (
    id uuid NOT NULL,
    tenant_id uuid,
    event_name character varying,
    first_seen timestamp without time zone,
    last_seen timestamp without time zone,
    total_events bigint,
    description text,
    metadata jsonb
);


--
-- Name: events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.events (
    id uuid NOT NULL,
    tenant_id uuid,
    event_name character varying,
    user_id character varying,
    "timestamp" timestamp without time zone,
    properties jsonb,
    ingested_at timestamp without time zone
);


--
-- Name: insights; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.insights (
    id uuid NOT NULL,
    tenant_id uuid,
    anomaly_id uuid,
    trend_id uuid,
    title character varying,
    summary text,
    explanation text,
    confidence double precision,
    created_at timestamp without time zone
);


--
-- Name: messages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.messages (
    id uuid NOT NULL,
    conversation_id uuid,
    sender character varying,
    message text,
    metadata jsonb,
    created_at timestamp without time zone
);


--
-- Name: metric_baselines; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.metric_baselines (
    id uuid NOT NULL,
    tenant_id uuid,
    metric_name character varying,
    tags jsonb,
    day_of_week smallint,
    hour_of_day smallint,
    avg_value double precision,
    stddev double precision,
    sample_size integer,
    computed_at timestamp without time zone
);


--
-- Name: metrics; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.metrics (
    id uuid NOT NULL,
    tenant_id uuid,
    metric_name character varying,
    metric_timestamp timestamp without time zone,
    value double precision,
    tags jsonb,
    created_at timestamp without time zone
);


--
-- Name: notifications; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.notifications (
    id uuid NOT NULL,
    tenant_id uuid,
    insight_id uuid,
    channel character varying,
    external_message_id character varying,
    delivered_at timestamp without time zone
);


--
-- Name: tenant_kafka_settings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tenant_kafka_settings (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    bootstrap_servers text,
    topic_include_pattern text,
    topic_exclude_pattern text DEFAULT '^__'::text NOT NULL,
    error_topic_pattern text DEFAULT '\.errors?$'::text NOT NULL,
    event_name_fields text[] DEFAULT ARRAY['event_name'::text, 'type'::text, 'action'::text, 'name'::text] NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL,
    security_protocol text,
    sasl_mechanism text,
    sasl_username text,
    sasl_password_encrypted text,
    sasl_password_updated_at timestamp without time zone,
    last_connect_at timestamp without time zone,
    last_connect_error text,
    last_message_at timestamp without time zone,
    last_message_topic text,
    messages_ingested_count bigint DEFAULT 0 NOT NULL
);


--
-- Name: tenants; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tenants (
    id uuid NOT NULL,
    name character varying,
    created_at timestamp without time zone,
    slack_channel character varying,
    sms_recipients jsonb
);


--
-- Name: trends; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trends (
    id uuid NOT NULL,
    tenant_id uuid,
    metric_name character varying,
    direction character varying,
    slope_per_hour double precision,
    change_percent_per_hour double precision,
    window_start timestamp without time zone,
    window_end timestamp without time zone,
    sample_size integer,
    mean_value double precision,
    detected_at timestamp without time zone,
    resolved_at timestamp without time zone,
    context jsonb
);


--
-- Name: anomalies anomalies_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.anomalies
    ADD CONSTRAINT anomalies_pkey PRIMARY KEY (id);


--
-- Name: conversations conversations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversations
    ADD CONSTRAINT conversations_pkey PRIMARY KEY (id);


--
-- Name: errors errors_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.errors
    ADD CONSTRAINT errors_pkey PRIMARY KEY (id);


--
-- Name: event_types event_types_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_types
    ADD CONSTRAINT event_types_pkey PRIMARY KEY (id);


--
-- Name: events events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.events
    ADD CONSTRAINT events_pkey PRIMARY KEY (id);


--
-- Name: insights insights_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.insights
    ADD CONSTRAINT insights_pkey PRIMARY KEY (id);


--
-- Name: messages messages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT messages_pkey PRIMARY KEY (id);


--
-- Name: metric_baselines metric_baselines_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.metric_baselines
    ADD CONSTRAINT metric_baselines_pkey PRIMARY KEY (id);


--
-- Name: metrics metrics_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.metrics
    ADD CONSTRAINT metrics_pkey PRIMARY KEY (id);


--
-- Name: notifications notifications_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_pkey PRIMARY KEY (id);


--
-- Name: tenant_kafka_settings tenant_kafka_settings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_kafka_settings
    ADD CONSTRAINT tenant_kafka_settings_pkey PRIMARY KEY (id);


--
-- Name: tenant_kafka_settings tenant_kafka_settings_tenant_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_kafka_settings
    ADD CONSTRAINT tenant_kafka_settings_tenant_id_key UNIQUE (tenant_id);


--
-- Name: tenants tenants_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenants
    ADD CONSTRAINT tenants_pkey PRIMARY KEY (id);


--
-- Name: trends trends_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trends
    ADD CONSTRAINT trends_pkey PRIMARY KEY (id);


--
-- Name: errors_tenant_fingerprint_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX errors_tenant_fingerprint_idx ON public.errors USING btree (tenant_id, fingerprint) WHERE (fingerprint IS NOT NULL);


--
-- Name: errors_tenant_service_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX errors_tenant_service_idx ON public.errors USING btree (tenant_id, service);


--
-- Name: errors_tenant_severity_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX errors_tenant_severity_idx ON public.errors USING btree (tenant_id, severity);


--
-- Name: errors_tenant_unresolved_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX errors_tenant_unresolved_idx ON public.errors USING btree (tenant_id, resolved_at) WHERE (resolved_at IS NULL);


--
-- Name: event_types_tenant_id_event_name_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX event_types_tenant_id_event_name_idx ON public.event_types USING btree (tenant_id, event_name);


--
-- Name: anomalies anomalies_metric_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.anomalies
    ADD CONSTRAINT anomalies_metric_id_fkey FOREIGN KEY (metric_id) REFERENCES public.metrics(id) DEFERRABLE;


--
-- Name: anomalies anomalies_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.anomalies
    ADD CONSTRAINT anomalies_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) DEFERRABLE;


--
-- Name: conversations conversations_insight_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversations
    ADD CONSTRAINT conversations_insight_id_fkey FOREIGN KEY (insight_id) REFERENCES public.insights(id) DEFERRABLE;


--
-- Name: conversations conversations_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversations
    ADD CONSTRAINT conversations_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) DEFERRABLE;


--
-- Name: errors errors_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.errors
    ADD CONSTRAINT errors_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) DEFERRABLE;


--
-- Name: event_types event_types_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_types
    ADD CONSTRAINT event_types_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) DEFERRABLE;


--
-- Name: events events_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.events
    ADD CONSTRAINT events_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) DEFERRABLE;


--
-- Name: insights insights_anomaly_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.insights
    ADD CONSTRAINT insights_anomaly_id_fkey FOREIGN KEY (anomaly_id) REFERENCES public.anomalies(id) DEFERRABLE;


--
-- Name: insights insights_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.insights
    ADD CONSTRAINT insights_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) DEFERRABLE;


--
-- Name: insights insights_trend_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.insights
    ADD CONSTRAINT insights_trend_id_fkey FOREIGN KEY (trend_id) REFERENCES public.trends(id) DEFERRABLE;


--
-- Name: messages messages_conversation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT messages_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES public.conversations(id) DEFERRABLE;


--
-- Name: metric_baselines metric_baselines_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.metric_baselines
    ADD CONSTRAINT metric_baselines_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) DEFERRABLE;


--
-- Name: metrics metrics_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.metrics
    ADD CONSTRAINT metrics_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) DEFERRABLE;


--
-- Name: notifications notifications_insight_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_insight_id_fkey FOREIGN KEY (insight_id) REFERENCES public.insights(id) DEFERRABLE;


--
-- Name: notifications notifications_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) DEFERRABLE;


--
-- Name: tenant_kafka_settings tenant_kafka_settings_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_kafka_settings
    ADD CONSTRAINT tenant_kafka_settings_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) DEFERRABLE;


--
-- Name: trends trends_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trends
    ADD CONSTRAINT trends_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) DEFERRABLE;


--
-- PostgreSQL database dump complete
--


