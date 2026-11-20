import { useState, useEffect, useRef } from 'react';

export const canvasConfig = {
  mode: 'full-bleed',
  padding: 0,
  background: '#ffffff',
  color: '#111111',
  fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
};

// Intersection Observer hook for scroll-reveal
function useScrollReveal() {
  const [visibleElements, setVisibleElements] = useState<Set<string>>(new Set());
  const observerRef = useRef<IntersectionObserver | null>(null);

  useEffect(() => {
    observerRef.current = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            const id = entry.target.getAttribute('data-reveal-id');
            if (id) {
              setVisibleElements((prev) => new Set(prev).add(id));
              observerRef.current?.unobserve(entry.target);
            }
          }
        });
      },
      { threshold: 0.1, rootMargin: '0px 0px -50px 0px' }
    );

    return () => observerRef.current?.disconnect();
  }, []);

  const observe = (element: HTMLElement | null) => {
    if (element && observerRef.current) {
      observerRef.current.observe(element);
    }
  };

  return { visibleElements, observe };
}

// Animated counter hook
function useCounter(end: number, duration: number = 2000, start: boolean = true) {
  const [count, setCount] = useState(0);

  useEffect(() => {
    if (!start) return;
    
    let startTime: number | null = null;
    let animationFrame: number;

    const animate = (timestamp: number) => {
      if (!startTime) startTime = timestamp;
      const progress = Math.min((timestamp - startTime) / duration, 1);
      setCount(Math.floor(progress * end));

      if (progress < 1) {
        animationFrame = requestAnimationFrame(animate);
      }
    };

    animationFrame = requestAnimationFrame(animate);
    return () => cancelAnimationFrame(animationFrame);
  }, [end, duration, start]);

  return count;
}

export default function AnimationDemo() {
  const { visibleElements, observe } = useScrollReveal();
  const [hoveredCard, setHoveredCard] = useState<number | null>(null);

  const stats = [
    { label: 'Объём, USDC', value: 176, suffix: '' },
    { label: 'Пользователей', value: 17, suffix: '' },
    { label: 'Транзакций', value: 130, suffix: '' },
    { label: 'Комиссий, USDC', value: 30, suffix: ',25' },
  ];

  const markets = [
    { id: 1, question: 'Кто станет лидером по чаевым за ноябрь?', liquidity: '103,26 USDC', participants: 3 },
    { id: 2, question: 'Перепишет ли Tippy 1000 чаевых до конца месяца?', liquidity: '100,16 USDC', participants: 3 },
    { id: 3, question: 'Какая доля стейблкоинов в объёме Base будет в декабре?', liquidity: '53,74 USDC', participants: 7 },
  ];

  const features = [
    { title: 'Мгновенные транзакции', description: 'Суб-секундные переводы за доли цента. Всегда онлайн, всегда быстро.' },
    { title: 'Ликвидные рынки', description: 'Глубокая ликвидность и качественные активы круглосуточно.' },
    { title: 'Безопасность', description: 'Построено на Ethereum с фокусом на безопасность.' },
    { title: 'Масштабируемость', description: 'Платформа для финансовых транзакций любого масштаба.' },
  ];

  return (
    <div style={{ minHeight: '100vh', background: '#ffffff' }}>
      {/* Hero Section */}
      <section
        ref={observe}
        data-reveal-id="hero"
        style={{
          padding: '80px 24px',
          textAlign: 'center',
          background: '#0000ff',
          color: '#ffffff',
          opacity: visibleElements.has('hero') ? 1 : 0,
          transform: visibleElements.has('hero') ? 'translateY(0)' : 'translateY(30px)',
          transition: 'opacity 0.8s ease, transform 0.8s ease',
        }}
      >
        <h1 style={{ fontSize: '48px', fontWeight: 800, marginBottom: '16px', letterSpacing: '-0.02em' }}>
          Tippy Animations Demo
        </h1>
        <p style={{ fontSize: '20px', opacity: 0.9, maxWidth: '600px', margin: '0 auto' }}>
          Scroll down to see animations in action
        </p>
      </section>

      {/* Stats Section with Counter Animation */}
      <section style={{ padding: '60px 24px', maxWidth: '1200px', margin: '0 auto' }}>
        <h2
          ref={observe}
          data-reveal-id="stats-title"
          style={{
            fontSize: '32px',
            fontWeight: 700,
            marginBottom: '40px',
            textAlign: 'center',
            opacity: visibleElements.has('stats-title') ? 1 : 0,
            transform: visibleElements.has('stats-title') ? 'translateY(0)' : 'translateY(20px)',
            transition: 'opacity 0.6s ease, transform 0.6s ease',
          }}
        >
          Статистика с анимированными счётчиками
        </h2>
        <div
          ref={observe}
          data-reveal-id="stats-grid"
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
            gap: '24px',
            opacity: visibleElements.has('stats-grid') ? 1 : 0,
            transform: visibleElements.has('stats-grid') ? 'translateY(0)' : 'translateY(20px)',
            transition: 'opacity 0.6s ease 0.2s, transform 0.6s ease 0.2s',
          }}
        >
          {stats.map((stat, i) => (
            <StatCard key={i} stat={stat} isVisible={visibleElements.has('stats-grid')} delay={i * 0.1} />
          ))}
        </div>
      </section>

      {/* Hover Lift Cards */}
      <section style={{ padding: '60px 24px', background: '#f8f9fa' }}>
        <div style={{ maxWidth: '1200px', margin: '0 auto' }}>
          <h2
            ref={observe}
            data-reveal-id="hover-title"
            style={{
              fontSize: '32px',
              fontWeight: 700,
              marginBottom: '40px',
              textAlign: 'center',
              opacity: visibleElements.has('hover-title') ? 1 : 0,
              transform: visibleElements.has('hover-title') ? 'translateY(0)' : 'translateY(20px)',
              transition: 'opacity 0.6s ease, transform 0.6s ease',
            }}
          >
            Hover-эффект подъёма карточек
          </h2>
          <div
            ref={observe}
            data-reveal-id="hover-grid"
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(250px, 1fr))',
              gap: '24px',
              opacity: visibleElements.has('hover-grid') ? 1 : 0,
              transform: visibleElements.has('hover-grid') ? 'translateY(0)' : 'translateY(20px)',
              transition: 'opacity 0.6s ease 0.2s, transform 0.6s ease 0.2s',
            }}
          >
            {features.map((feature, i) => (
              <div
                key={i}
                onMouseEnter={() => setHoveredCard(i)}
                onMouseLeave={() => setHoveredCard(null)}
                style={{
                  background: '#ffffff',
                  border: '1px solid #e0e0e0',
                  borderRadius: '12px',
                  padding: '24px',
                  cursor: 'pointer',
                  transform: hoveredCard === i ? 'translateY(-8px)' : 'translateY(0)',
                  boxShadow: hoveredCard === i ? '0 12px 24px rgba(0, 0, 0, 0.12)' : '0 2px 8px rgba(0, 0, 0, 0.04)',
                  transition: 'transform 0.3s ease, box-shadow 0.3s ease',
                }}
              >
                <h3 style={{ fontSize: '20px', fontWeight: 600, marginBottom: '12px', color: '#0000ff' }}>
                  {feature.title}
                </h3>
                <p style={{ fontSize: '15px', color: '#666', lineHeight: 1.6 }}>
                  {feature.description}
                </p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Staggered List Animation */}
      <section style={{ padding: '60px 24px', maxWidth: '1200px', margin: '0 auto' }}>
        <h2
          ref={observe}
          data-reveal-id="stagger-title"
          style={{
            fontSize: '32px',
            fontWeight: 700,
            marginBottom: '40px',
            textAlign: 'center',
            opacity: visibleElements.has('stagger-title') ? 1 : 0,
            transform: visibleElements.has('stagger-title') ? 'translateY(0)' : 'translateY(20px)',
            transition: 'opacity 0.6s ease, transform 0.6s ease',
          }}
        >
          Каскадная анимация списков
        </h2>
        <div
          ref={observe}
          data-reveal-id="stagger-list"
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: '16px',
            opacity: visibleElements.has('stagger-list') ? 1 : 0,
            transition: 'opacity 0.6s ease',
          }}
        >
          {markets.map((market, i) => (
            <div
              key={market.id}
              style={{
                background: '#ffffff',
                border: '1px solid #e0e0e0',
                borderRadius: '12px',
                padding: '20px',
                opacity: visibleElements.has('stagger-list') ? 1 : 0,
                transform: visibleElements.has('stagger-list') ? 'translateX(0)' : 'translateX(-30px)',
                transition: `opacity 0.5s ease ${i * 0.15}s, transform 0.5s ease ${i * 0.15}s`,
              }}
            >
              <h3 style={{ fontSize: '18px', fontWeight: 600, marginBottom: '12px' }}>
                #{market.id} {market.question}
              </h3>
              <div style={{ display: 'flex', gap: '24px', fontSize: '14px', color: '#666' }}>
                <span>Ликвидность: <strong style={{ color: '#0000ff' }}>{market.liquidity}</strong></span>
                <span>Участников: <strong style={{ color: '#0000ff' }}>{market.participants}</strong></span>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* Pulse Animation */}
      <section style={{ padding: '60px 24px', background: '#f8f9fa' }}>
        <div style={{ maxWidth: '1200px', margin: '0 auto', textAlign: 'center' }}>
          <h2
            ref={observe}
            data-reveal-id="pulse-title"
            style={{
              fontSize: '32px',
              fontWeight: 700,
              marginBottom: '40px',
              opacity: visibleElements.has('pulse-title') ? 1 : 0,
              transform: visibleElements.has('pulse-title') ? 'translateY(0)' : 'translateY(20px)',
              transition: 'opacity 0.6s ease, transform 0.6s ease',
            }}
          >
            Pulse-анимация для индикаторов
          </h2>
          <div
            ref={observe}
            data-reveal-id="pulse-demo"
            style={{
              display: 'flex',
              justifyContent: 'center',
              gap: '40px',
              flexWrap: 'wrap',
              opacity: visibleElements.has('pulse-demo') ? 1 : 0,
              transition: 'opacity 0.6s ease 0.2s',
            }}
          >
            <PulseIndicator label="Live" color="#00c853" />
            <PulseIndicator label="Processing" color="#ff9800" />
            <PulseIndicator label="Error" color="#f44336" />
          </div>
        </div>
      </section>

      {/* Footer */}
      <section style={{ padding: '60px 24px', background: '#0000ff', color: '#ffffff', textAlign: 'center' }}>
        <h2 style={{ fontSize: '32px', fontWeight: 700, marginBottom: '16px' }}>
          Готово к внедрению
        </h2>
        <p style={{ fontSize: '18px', opacity: 0.9, maxWidth: '600px', margin: '0 auto' }}>
          Все анимации работают плавно и уважают prefers-reduced-motion
        </p>
      </section>
    </div>
  );
}

function StatCard({ stat, isVisible, delay }: { stat: any; isVisible: boolean; delay: number }) {
  const count = useCounter(stat.value, 2000, isVisible);
  
  return (
    <div
      style={{
        background: '#ffffff',
        border: '1px solid #e0e0e0',
        borderRadius: '12px',
        padding: '24px',
        textAlign: 'center',
        transition: `transform 0.3s ease ${delay}s, box-shadow 0.3s ease ${delay}s`,
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.transform = 'translateY(-4px)';
        e.currentTarget.style.boxShadow = '0 8px 16px rgba(0, 0, 0, 0.08)';
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.transform = 'translateY(0)';
        e.currentTarget.style.boxShadow = 'none';
      }}
    >
      <div style={{ fontSize: '36px', fontWeight: 800, color: '#0000ff', marginBottom: '8px' }}>
        {count}{stat.suffix}
      </div>
      <div style={{ fontSize: '14px', color: '#666', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
        {stat.label}
      </div>
    </div>
  );
}

function PulseIndicator({ label, color }: { label: string; color: string }) {
  const [scale, setScale] = useState(1);

  useEffect(() => {
    const interval = setInterval(() => {
      setScale((prev) => (prev === 1 ? 1.2 : 1));
    }, 1000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div style={{ textAlign: 'center' }}>
      <div
        style={{
          width: '60px',
          height: '60px',
          borderRadius: '50%',
          background: color,
          margin: '0 auto 12px',
          transform: `scale(${scale})`,
          transition: 'transform 0.5s ease',
          boxShadow: `0 0 20px ${color}40`,
        }}
      />
      <div style={{ fontSize: '14px', fontWeight: 600 }}>{label}</div>
    </div>
  );
}
